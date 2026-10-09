#!/usr/bin/env python3
"""
SWAT+ ↔ MODFLOW 6 Coupling Framework (One-Way & Two-Way)

Orchestrates bidirectional hydrologic-groundwater coupling:
  - One-way: SWAT+ outputs (recharge, streamflow) drive MODFLOW6
  - Two-way: GW heads feed back to SWAT+ baseflow routing

Monthly stress period coupling:
  1. SWAT+ writes monthly outputs (channel flow, HRU water balance)
  2. Convert SWAT+ outputs → MODFLOW inputs (RCH, SFR/RIV)
  3. Run MODFLOW6 for all periods
  4. (Two-way only) Extract GW heads → convert to HRU water tables → write to SWAT+

Prerequisites:
    pip install flopy pandas numpy
    MODFLOW6 executable (download from USGS)
    SWAT+ monthly outputs enabled (channel_sd_mon, hru_wb_mon)

Usage:
    # One-way coupling
    python run_coupled_model.py \
      --swat-dir /path/to/swat/txtinout \
      --swat-exe /path/to/swatplus/exe \
      --mf6-exe /path/to/mf6 \
      --mf6-model /path/to/modflow/model.nam \
      --start-year 1995 \
      --end-year 2019

    # Two-way coupling with convergence check
    python run_coupled_model.py \
      --swat-dir /path/to/swat/txtinout \
      --swat-exe /path/to/swatplus/exe \
      --mf6-exe /path/to/mf6 \
      --mf6-model /path/to/modflow/model.nam \
      --start-year 1995 \
      --end-year 2019 \
      --two-way \
      --iterations 3

References:
    - Bailey et al. (2020) SWAT+MF workflow
    - Kim et al. (2008) SWAT-MODFLOW coupling formulation
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import flopy

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def _resolve_path(arg_val, env_var: str, default: str = None) -> Path:
    """Resolve a path: CLI arg → env var → default."""
    if arg_val:
        return Path(arg_val)
    if env_var:
        env = os.environ.get(env_var)
        if env:
            return Path(env)
    if default:
        return Path(default)
    raise ValueError(f"No path provided for {env_var}")


class SWATModflowCoupler:
    """Orchestrates SWAT+/MODFLOW6 coupling."""

    def __init__(
        self,
        swat_dir: Path,
        swat_exe: Path,
        mf6_exe: Path,
        mf6_model: Path,
        start_year: int,
        end_year: int,
        two_way: bool = False,
        iterations: int = 1,
        use_sfr: bool = True,
        out_dir: Optional[Path] = None,
    ):
        """
        Initialize coupler.

        Parameters
        ----------
        swat_dir : Path
            SWAT+ TxtInOut directory
        swat_exe : Path
            SWAT+ executable
        mf6_exe : Path
            MODFLOW6 executable
        mf6_model : Path
            MODFLOW6 model namefile
        start_year : int
            First year of simulation
        end_year : int
            Last year of simulation
        two_way : bool
            If True, GW heads feed back to SWAT+ baseflow
        iterations : int
            Number of two-way iterations (default: 1)
        use_sfr : bool
            Use SFR package (True) or RIV package (False)
        out_dir : Path, optional
            Output directory for coupled results
        """
        self.swat_dir = Path(swat_dir)
        self.swat_exe = Path(swat_exe)
        self.mf6_exe = Path(mf6_exe)
        self.mf6_model = Path(mf6_model)
        self.start_year = start_year
        self.end_year = end_year
        self.two_way = two_way
        self.iterations = iterations if two_way else 1
        self.use_sfr = use_sfr
        self.out_dir = Path(out_dir) if out_dir else Path("./models/coupled")
        self.out_dir.mkdir(parents=True, exist_ok=True)

        # Validate
        if not self.swat_dir.exists():
            raise FileNotFoundError(f"SWAT+ directory not found: {self.swat_dir}")
        if not self.swat_exe.exists():
            raise FileNotFoundError(f"SWAT+ executable not found: {self.swat_exe}")
        if not self.mf6_exe.exists():
            raise FileNotFoundError(f"MODFLOW6 executable not found: {self.mf6_exe}")
        if not self.mf6_model.exists():
            raise FileNotFoundError(f"MODFLOW6 model file not found: {self.mf6_model}")

        log.info("=" * 70)
        log.info("SWAT+/MODFLOW6 Coupling Initialization")
        log.info("=" * 70)
        log.info(f"SWAT+ directory: {self.swat_dir}")
        log.info(f"MODFLOW6 model: {self.mf6_model}")
        log.info(f"Period: {start_year}–{end_year} ({end_year - start_year + 1} years)")
        log.info(f"Stream routing: {'SFR' if use_sfr else 'RIV'}")
        log.info(f"Coupling mode: {'Two-way' if two_way else 'One-way'}")
        if two_way:
            log.info(f"Iterations: {iterations}")

    def run_one_way(self) -> dict:
        """
        Execute one-way coupling (SWAT+ → MODFLOW6).

        Returns
        -------
        dict
            Results including paths to head solutions and budget files
        """
        log.info("\n" + "=" * 70)
        log.info("ONE-WAY COUPLING: SWAT+ → MODFLOW6")
        log.info("=" * 70)

        # TODO: Implement
        # 1. Read SWAT+ outputs (monthly recharge, channel flow)
        # 2. Load MODFLOW6 model
        # 3. Build RCH/SFR packages from SWAT+ data
        # 4. Run transient simulation
        # 5. Extract and save results

        log.info("One-way coupling framework initialized. Implementation pending.")
        return {"heads": None, "budget": None}

    def run_two_way(self) -> dict:
        """
        Execute two-way coupling with convergence checking.

        Returns
        -------
        dict
            Converged results including iteration metrics
        """
        log.info("\n" + "=" * 70)
        log.info("TWO-WAY COUPLING: SWAT+ ↔ MODFLOW6")
        log.info("=" * 70)

        results = {}

        for iteration in range(self.iterations):
            log.info(f"\n--- Iteration {iteration} ---")

            # Iteration 0 = one-way
            if iteration == 0:
                iter_results = self.run_one_way()
            else:
                # Extract GW heads, feed back to SWAT+
                log.info(f"Iteration {iteration}: Extracting GW heads and updating SWAT+ ICs...")
                # TODO: Implement
                # 1. Load previous MODFLOW6 head solution
                # 2. Convert to HRU water table depths
                # 3. Write to SWAT+ shallow_aq.sha
                # 4. Re-run SWAT+
                # 5. Re-run MODFLOW6
                iter_results = self.run_one_way()

            # Convergence check
            if iteration > 0:
                # TODO: Compare recharge between iterations
                # delta_recharge = abs(iter_results['rch'] - prev_rch).mean()
                # log.info(f"Δ mean recharge: {delta_recharge:.2%}")
                # if delta_recharge < 0.01:  # 1% tolerance
                #     log.info("Converged!")
                #     break
                pass

            results[f"iteration_{iteration}"] = iter_results

        return results

    def run(self) -> dict:
        """Execute coupling (one-way or two-way)."""
        if self.two_way:
            return self.run_two_way()
        else:
            return self.run_one_way()


def main():
    ap = argparse.ArgumentParser(description=__doc__)

    # Required arguments
    ap.add_argument("--swat-dir", required=True, type=Path,
                   help="Path to SWAT+ TxtInOut directory")
    ap.add_argument("--swat-exe", required=True, type=Path,
                   help="Path to SWAT+ executable")
    ap.add_argument("--mf6-exe", required=True, type=Path,
                   help="Path to MODFLOW6 executable (mf6)")
    ap.add_argument("--mf6-model", required=True, type=Path,
                   help="Path to MODFLOW6 model namefile (.nam)")
    ap.add_argument("--start-year", required=True, type=int,
                   help="First year of simulation")
    ap.add_argument("--end-year", required=True, type=int,
                   help="Last year of simulation")

    # Coupling options
    ap.add_argument("--two-way", action="store_true",
                   help="Enable two-way coupling with GW feedback")
    ap.add_argument("--iterations", type=int, default=3,
                   help="Max two-way iterations (default: 3)")
    ap.add_argument("--sfr", action="store_true", default=True,
                   help="Use SFR package (default: True)")
    ap.add_argument("--riv", action="store_false", dest="sfr",
                   help="Use RIV package instead of SFR")
    ap.add_argument("--out-dir", type=Path, default=None,
                   help="Output directory (default: ./models/coupled)")

    args = ap.parse_args()

    # Create coupler and run
    coupler = SWATModflowCoupler(
        swat_dir=args.swat_dir,
        swat_exe=args.swat_exe,
        mf6_exe=args.mf6_exe,
        mf6_model=args.mf6_model,
        start_year=args.start_year,
        end_year=args.end_year,
        two_way=args.two_way,
        iterations=args.iterations,
        use_sfr=args.sfr,
        out_dir=args.out_dir,
    )

    results = coupler.run()
    log.info("\n" + "=" * 70)
    log.info("Coupling Complete")
    log.info("=" * 70)


if __name__ == "__main__":
    import os

    main()
