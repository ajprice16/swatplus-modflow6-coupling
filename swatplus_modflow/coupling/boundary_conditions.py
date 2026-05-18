"""Dynamic boundary conditions for MODFLOW 6

Manages daily stress period updates from SWAT+ to MODFLOW 6 boundary
condition packages (RCH, EVT, WEL, RIV, DRN).  Each method converts
the cell-based dicts produced by FluxTranslator into the FloPy stress
period data structures expected by MODFLOW 6.

References:
    Bailey et al. (2025) – SWAT+MODFLOW, GMD 18, 5681-5697.
    Langevin et al. (2017) – MODFLOW 6, USGS TM 6-A55.
"""

from typing import Dict, Any, Optional, List, Tuple
import logging
import numpy as np

logger = logging.getLogger(__name__)


class BoundaryConditionManager:
    """
    Manages dynamic boundary conditions that change each timestep.

    Daily Updates:
    - Recharge package (RCH): soil percolation from SWAT+ HRUs
    - Evapotranspiration package (EVT): unsatisfied ET demand
    - Well package (WEL): irrigation pumping demand
    - River package (RIV): channel/canal stage and exchange
    - Drain package (DRN): tile drain outflow
    - Constant Head (CHD): boundary conditions at model edges
    """

    def __init__(self, modflow_model, grid_info: Dict[str, Any]):
        """
        Initialize with a loaded FloPy MODFLOW 6 model.

        Args:
            modflow_model: FloPy MFModel (the GWF model object)
            grid_info: dict with 'nrow', 'ncol', 'nlayers'
        """
        self.model = modflow_model
        self.grid_info = grid_info

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _cell_id_to_indices(self, cell_id: int) -> Tuple[int, int, int]:
        """Convert 0-based linear cell id → (layer, row, col)."""
        nrow = self.grid_info.get('nrow', 1)
        ncol = self.grid_info.get('ncol', 1)
        k = cell_id // (nrow * ncol)
        remainder = cell_id % (nrow * ncol)
        i = remainder // ncol
        j = remainder % ncol
        return (k, i, j)

    # ------------------------------------------------------------------
    # RCH
    # ------------------------------------------------------------------
    def update_recharge_package(
        self,
        cell_recharge: Dict[int, float],
        stress_period: int = 0
    ) -> None:
        """
        Update RCH package with daily recharge rates.

        MODFLOW 6 RCH accepts either a 3-D recharge array (length/time)
        or list-based stress period data.  We build a 3-D array when the
        package uses array-based input, otherwise build list records.

        Args:
            cell_recharge: Cell ID → recharge rate (m³/day).  Internally
                           converted to length/time (m/day) by dividing
                           by cell area before setting array values.
            stress_period: 0-based stress period index to update.
        """
        rch = getattr(self.model, 'rch', None)
        if rch is None:
            return

        try:
            # Determine cell area for rate → flux conversion
            dx = self.grid_info.get('dx', 500.0)
            dy = self.grid_info.get('dy', 500.0)
            cell_area = dx * dy  # m²

            nlay = self.grid_info.get('nlayers', 1)
            nrow = self.grid_info.get('nrow', 1)
            ncol = self.grid_info.get('ncol', 1)

            # Build recharge array (m/day flux)
            rch_array = np.zeros((nlay, nrow, ncol), dtype=float)
            for cell_id, rate in cell_recharge.items():
                k, i, j = self._cell_id_to_indices(cell_id)
                # rate is volumetric (m³/day) → convert to flux (m/day)
                rch_array[k, i, j] += rate / cell_area

            rch.recharge.set_data(rch_array[0], key=stress_period)
            logger.debug(f"Updated RCH: {len(cell_recharge)} cells, SP {stress_period}")
        except Exception as e:
            logger.warning(f"Could not update RCH package: {e}")

    # ------------------------------------------------------------------
    # EVT
    # ------------------------------------------------------------------
    def update_et_package(
        self,
        cell_et: Dict[int, float],
        extinction_depth_m: float = 3.0,
        cell_elevation_m: Optional[Dict[int, float]] = None,
        stress_period: int = 0
    ) -> None:
        """
        Update EVT package with ET extraction rates.

        Per Bailey 2025 §2.3.3, groundwater ET is calculated using a
        linear relationship between the water table elevation and a
        specified extinction depth below ground surface.

        Args:
            cell_et: Cell ID → max ET rate (m³/day)
            extinction_depth_m: depth below which ET cannot occur
            cell_elevation_m: Cell ID → land surface elevation (m)
            stress_period: 0-based stress period index to update.
        """
        evt = getattr(self.model, 'evt', None)
        if evt is None:
            return

        try:
            dx = self.grid_info.get('dx', 500.0)
            dy = self.grid_info.get('dy', 500.0)
            cell_area = dx * dy

            nrow = self.grid_info.get('nrow', 1)
            ncol = self.grid_info.get('ncol', 1)

            # Build list-based SPD: [(layer, row, col), surface, rate, depth]
            spd_list = []
            for cell_id, rate in cell_et.items():
                k, i, j = self._cell_id_to_indices(cell_id)
                surface = (cell_elevation_m or {}).get(cell_id, 0.0)
                et_flux = rate / cell_area  # m/day
                spd_list.append(((k, i, j), surface, et_flux, extinction_depth_m))

            if spd_list:
                evt.stress_period_data.set_data(spd_list, key=stress_period)
            logger.debug(f"Updated EVT: {len(cell_et)} cells, SP {stress_period}")
        except Exception as e:
            logger.warning(f"Could not update EVT package: {e}")

    # ------------------------------------------------------------------
    # WEL
    # ------------------------------------------------------------------
    def update_well_package(
        self,
        cell_pumping: Dict[int, float],
        stress_period: int = 0
    ) -> None:
        """
        Update WEL package with pumping rates.

        Per Bailey 2025 §2.3.5, irrigation pumping is demand-driven:
        SWAT+ triggers demand, available groundwater in the cell is
        checked, and the withdrawal volume is applied to the WEL package.

        Args:
            cell_pumping: Cell ID → pumping rate (m³/day, negative = extraction)
            stress_period: 0-based stress period index to update.
        """
        wel = getattr(self.model, 'wel', None)
        if wel is None:
            return

        try:
            # Build WEL SPD: [(layer, row, col), q]
            spd_list = []
            for cell_id, rate in cell_pumping.items():
                if rate == 0.0:
                    continue
                k, i, j = self._cell_id_to_indices(cell_id)
                spd_list.append(((k, i, j), rate))

            if spd_list:
                wel.stress_period_data.set_data(spd_list, key=stress_period)
            else:
                # No pumping this period — set empty
                wel.stress_period_data.set_data(None, key=stress_period)
            logger.debug(f"Updated WEL: {len(cell_pumping)} wells, SP {stress_period}")
        except Exception as e:
            logger.warning(f"Could not update WEL package: {e}")

    # ------------------------------------------------------------------
    # RIV  (channels + canals)
    # ------------------------------------------------------------------
    def update_river_package(
        self,
        river_data: Dict[int, Dict[str, float]],
        stress_period: int = 0
    ) -> None:
        """
        Update RIV package with stage, conductance, and bottom elevation.

        Per Bailey 2025 §2.3.4 and §2.3.6, the River package is used for
        both channel–aquifer and canal–aquifer exchange.  Darcy's Law
        governs the exchange:
            Q = K_bed * L * W / d_bed * (h_stream − h_gw)

        Args:
            river_data: Cell ID → {'stage': m, 'conductance': m²/day,
                        'rbot': m} – if conductance/rbot are absent,
                        existing package values are preserved.
            stress_period: 0-based stress period index to update.
        """
        riv = getattr(self.model, 'riv', None)
        if riv is None:
            return

        try:
            # Get existing RIV data to preserve conductance/rbot where
            # only stage is being updated.
            existing = {}
            try:
                current_spd = riv.stress_period_data.get_data(key=stress_period)
                if current_spd is not None:
                    for rec in current_spd:
                        cellid = tuple(rec['cellid']) if 'cellid' in rec.dtype.names else (rec[0], rec[1], rec[2])
                        existing[cellid] = {
                            'stage': float(rec['stage']),
                            'cond': float(rec['cond']),
                            'rbot': float(rec['rbot']),
                        }
            except Exception:
                pass

            spd_list = []
            for cell_id, vals in river_data.items():
                k, i, j = self._cell_id_to_indices(cell_id)
                cellid = (k, i, j)

                stage = vals.get('stage', 0.0)
                cond = vals.get('conductance',
                                existing.get(cellid, {}).get('cond', 100.0))
                rbot = vals.get('rbot',
                                existing.get(cellid, {}).get('rbot', stage - 1.0))
                spd_list.append((cellid, stage, cond, rbot))

            if spd_list:
                riv.stress_period_data.set_data(spd_list, key=stress_period)
            logger.debug(f"Updated RIV: {len(river_data)} reaches, SP {stress_period}")
        except Exception as e:
            logger.warning(f"Could not update RIV package: {e}")

    # ------------------------------------------------------------------
    # DRN
    # ------------------------------------------------------------------
    def update_drain_package(
        self,
        drain_data: Dict[int, Dict[str, float]],
        stress_period: int = 0
    ) -> None:
        """
        Update DRN package with drain elevations and conductances.

        Per Bailey 2025 §2.3.7, if the water table is above the drain
        elevation, groundwater is removed and transferred to the nearest
        SWAT+ channel for routing.

        Args:
            drain_data: Cell ID → {'elev': m, 'cond': m²/day}
            stress_period: 0-based stress period index to update.
        """
        drn = getattr(self.model, 'drn', None)
        if drn is None:
            return

        try:
            spd_list = []
            for cell_id, vals in drain_data.items():
                k, i, j = self._cell_id_to_indices(cell_id)
                elev = vals.get('elev', 0.0)
                cond = vals.get('cond', 10.0)
                spd_list.append(((k, i, j), elev, cond))

            if spd_list:
                drn.stress_period_data.set_data(spd_list, key=stress_period)
            logger.debug(f"Updated DRN: {len(drain_data)} drains, SP {stress_period}")
        except Exception as e:
            logger.warning(f"Could not update DRN package: {e}")
