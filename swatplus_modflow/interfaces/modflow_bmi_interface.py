"""
MODFLOW 6 BMI interface via modflowapi (in-memory period stepping).

Replaces the subprocess write-run-read cycle with direct memory access,
which cuts I/O overhead and makes two-way coupling iteration faster.

Setup (conda-forge)::

    conda install -c conda-forge modflowapi

This installs modflowapi (Python bindings) and libmf6 (``libmf6.so`` /
``libmf6.dylib`` / ``libmf6.dll`` depending on platform). Pass the library
path to ``ModflowBMIInterface(lib_path=...)`` when it is not on the default
library search path.

Workflow for two-way coupling:
    1. Build MODFLOW input files once with FloPy.
    2. Open the BMI handle: ModflowBMIInterface(model_dir, lib_path, model_name=...)
    3. Call initialize() — loads the model into memory.
    4. For each stress period k:
         a. bmi.set_recharge(k, rch_array)   # (nrow, ncol)
         b. bmi.advance_period()              # solve one period
         c. heads = bmi.get_heads_layer1()    # (nrow, ncol)
    5. Call finalize() (or use as context manager).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    from modflowapi import ModflowApi
    _HAS_MODFLOWAPI = True
except ImportError:
    _HAS_MODFLOWAPI = False

# HDRY sentinel used by MODFLOW 6 for dry / inactive cells
_HDRY = 1.0e30


class ModflowBMIInterface:
    """
    In-memory MODFLOW 6 driver using the BMI (modflowapi).

    Parameters
    ----------
    model_dir:
        Directory containing mfsim.nam and all MODFLOW 6 input files.
    lib_path:
        Path to libmf6.so (Linux) or libmf6.dll (Windows).
        Defaults to ``libmf6.so`` on the system library path (works after
        ``conda install -c conda-forge modflowapi`` on Linux).
    model_name:
        Name of the GWF model as declared in mfsim.nam.  Must match the
        model name used when the FloPy / MF6 inputs were written.
    """

    def __init__(
        self,
        model_dir: str | Path,
        lib_path: Optional[str | Path] = None,
        model_name: str = "GWF",
    ) -> None:
        if not _HAS_MODFLOWAPI:
            raise ImportError(
                "modflowapi is not installed. Install with:\n"
                "  conda install -c conda-forge modflowapi\n"
                "or pip install modflowapi"
            )

        self.model_dir = Path(model_dir)
        self.lib_path = str(lib_path) if lib_path else "libmf6.so"
        self.model_name = model_name.upper()

        self._mf6: Optional[ModflowApi] = None
        self._nrow: int = 0
        self._ncol: int = 0
        self._nlay: int = 0
        self._ncells: int = 0  # nlay * nrow * ncol
        self._rch_addr: Optional[str] = None
        self._head_addr: Optional[str] = None
        self._current_period: int = 0

    # ------------------------------------------------------------------ setup

    def initialize(self) -> None:
        """Load the model into memory via the BMI."""
        nam = self.model_dir / "mfsim.nam"
        if not nam.exists():
            raise FileNotFoundError(f"mfsim.nam not found in {self.model_dir}")

        logger.info(f"Initializing MODFLOW 6 BMI from {nam}")
        self._mf6 = ModflowApi(self.lib_path, working_directory=str(self.model_dir))
        self._mf6.initialize(str(nam))

        self._discover_addresses()
        self._read_grid_dims()
        logger.info(
            f"BMI ready — model={self.model_name}  "
            f"grid={self._nlay}×{self._nrow}×{self._ncol}  "
            f"RCH={self._rch_addr}  heads={self._head_addr}"
        )

    def _discover_addresses(self) -> None:
        """Find the BMI variable addresses for RCH and heads."""
        addrs = self._mf6.get_input_var_names() + self._mf6.get_output_var_names()

        # Recharge package — MODFLOW 6 names it "RCH-1" or "RCH_0" etc.
        for addr in addrs:
            upper = addr.upper()
            if self.model_name in upper and "RCH" in upper and "RECHARGE" in upper:
                self._rch_addr = addr
                break

        # Solution vector (heads): lives in the solution object, not the model
        for addr in addrs:
            upper = addr.upper()
            if ("SLN" in upper or "SLN_1" in upper) and upper.endswith("/X"):
                self._head_addr = addr
                break
        # Fallback: some versions expose it directly on the model
        if self._head_addr is None:
            for addr in addrs:
                if addr.upper() == f"{self.model_name}/X":
                    self._head_addr = addr
                    break

        if self._rch_addr is None:
            logger.warning("RCH RECHARGE variable not found via BMI — set_recharge() will be a no-op")
        if self._head_addr is None:
            logger.warning("Head solution vector not found via BMI — get_heads_layer1() returns NaN array")

    def _read_grid_dims(self) -> None:
        """Read nlay / nrow / ncol from the BMI (NODES, NROW, NCOL, NLAY)."""
        try:
            self._nrow = int(self._mf6.get_value(f"{self.model_name}/DIS/NROW")[0])
            self._ncol = int(self._mf6.get_value(f"{self.model_name}/DIS/NCOL")[0])
            self._nlay = int(self._mf6.get_value(f"{self.model_name}/DIS/NLAY")[0])
        except Exception:
            # Older modflowapi versions: read NODEUSER count and assume 1 layer
            try:
                nodes = int(self._mf6.get_value(f"{self.model_name}/MSHAPE")[0])
                self._ncells = nodes
                logger.warning("Could not read NROW/NCOL/NLAY directly — grid shape unavailable")
                return
            except Exception as exc:
                logger.warning(f"Could not read grid dimensions: {exc}")
                return
        self._ncells = self._nlay * self._nrow * self._ncol

    # ------------------------------------------------------- per-period API

    def set_recharge(self, rch_array: np.ndarray) -> None:
        """
        Write a new recharge array (m/d) for the next stress period.

        Parameters
        ----------
        rch_array:
            2-D array of shape (nrow, ncol) in m/d.  Active cells only;
            inactive cells are ignored by MODFLOW.
        """
        if self._rch_addr is None or self._mf6 is None:
            return
        try:
            ptr = self._mf6.get_value_ptr(self._rch_addr)
            # ptr is 1-D, length = nrow * ncol (recharge is single-layer)
            flat = rch_array.ravel().astype(np.float64)
            expected = self._nrow * self._ncol
            if flat.size != expected:
                logger.warning(
                    f"set_recharge: array size {flat.size} != {expected}; skipping"
                )
                return
            ptr[:] = flat
        except Exception as exc:
            logger.warning(f"set_recharge failed: {exc}")

    def advance_period(self) -> bool:
        """
        Advance MODFLOW one full stress period (prepare → solve → finalize).

        Returns True if the solver converged, False otherwise.
        """
        if self._mf6 is None:
            raise RuntimeError("BMI not initialized — call initialize() first")

        # dt = 0.0 tells MODFLOW to use the timestep length from TDIS
        self._mf6.prepare_time_step(0.0)

        n_solutions = self._mf6.get_subcomponent_count()
        converged = True
        for sol_id in range(1, n_solutions + 1):
            self._mf6.prepare_solve(sol_id)
            max_iter = 100
            for _ in range(max_iter):
                has_converged = self._mf6.solve(sol_id)
                if has_converged:
                    break
            else:
                logger.warning(f"Period {self._current_period}: solver did not converge")
                converged = False
            self._mf6.finalize_solve(sol_id)

        self._mf6.finalize_time_step()
        self._current_period += 1
        return converged

    def get_heads_layer1(self) -> np.ndarray:
        """
        Return layer-1 heads as a (nrow, ncol) float64 array.

        Inactive / dry cells are set to NaN.
        """
        if self._mf6 is None or self._head_addr is None:
            shape = (self._nrow, self._ncol) if self._nrow else (1, 1)
            return np.full(shape, np.nan)

        try:
            x = self._mf6.get_value(self._head_addr).astype(np.float64)
            # x is 1-D: (nlay * nrow * ncol,) in row-major order
            if self._ncells > 0 and x.size == self._ncells:
                heads_3d = x.reshape(self._nlay, self._nrow, self._ncol)
            elif self._nrow and self._ncol:
                # Might be just nrow*ncol if 1-layer
                heads_3d = x.reshape(1, self._nrow, self._ncol)
            else:
                return np.full((1, 1), np.nan)

            layer1 = heads_3d[0].copy()
            layer1[np.abs(layer1) >= _HDRY * 0.1] = np.nan
            return layer1
        except Exception as exc:
            logger.warning(f"get_heads_layer1 failed: {exc}")
            return np.full((self._nrow or 1, self._ncol or 1), np.nan)

    def get_current_period(self) -> int:
        """Return the number of stress periods completed so far."""
        return self._current_period

    # --------------------------------------------------------- lifecycle

    def finalize(self) -> None:
        """Release the MODFLOW shared library handle."""
        if self._mf6 is not None:
            try:
                self._mf6.finalize()
            except Exception as exc:
                logger.warning(f"BMI finalize error: {exc}")
            finally:
                self._mf6 = None
        logger.info("MODFLOW 6 BMI finalized")

    def __enter__(self) -> "ModflowBMIInterface":
        return self

    def __exit__(self, *_) -> None:
        self.finalize()

    # ------------------------------------------------------- convenience

    @property
    def nrow(self) -> int:
        return self._nrow

    @property
    def ncol(self) -> int:
        return self._ncol

    @property
    def nlay(self) -> int:
        return self._nlay

    @staticmethod
    def is_available() -> bool:
        """Return True if modflowapi is installed and importable."""
        return _HAS_MODFLOWAPI
