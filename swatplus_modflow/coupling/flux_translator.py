"""Variable translation and mapping between SWAT+ and MODFLOW."""

from collections import defaultdict
import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


class FluxTranslator:
    """Translate hydrologic fluxes between SWAT+ and MODFLOW 6."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.recharge_delay_state: Dict[int, Dict[str, float]] = {}

    @staticmethod
    def _iter_connections(connections: Any):
        """Yield ``(cell_id, weight)`` pairs from a connection list."""
        if not connections:
            return

        for connection in connections:
            if isinstance(connection, (tuple, list)):
                if not connection:
                    continue
                if len(connection) == 1:
                    yield int(connection[0]), 1.0
                else:
                    yield int(connection[0]), float(connection[1])
            else:
                yield int(connection), 1.0

    def swat_recharge_to_modflow(
        self,
        recharge_hru: Dict[int, float],
        geo_map: Dict[int, List[Tuple[int, float]]],
        delay_days: float = 10.0,
        hru_areas_km2: Dict[int, float] = None,
    ) -> Dict[int, float]:
        """Translate SWAT+ recharge depth to MODFLOW recharge volume."""
        cell_recharge = defaultdict(float)

        for hru_id, recharge_mm in recharge_hru.items():
            if hru_id not in geo_map or recharge_mm <= 0:
                continue

            recharge_delayed = self._apply_recharge_delay(hru_id, recharge_mm, delay_days)
            hru_area_km2 = hru_areas_km2.get(hru_id, 1.0) if hru_areas_km2 else 1.0

            for cell_id, fraction in self._iter_connections(geo_map[hru_id]):
                recharge_m3_day = recharge_delayed * fraction * hru_area_km2 * 1000.0
                cell_recharge[cell_id] += recharge_m3_day

        return dict(cell_recharge)

    def _apply_recharge_delay(self, hru_id: int, dp_mm: float, delay_days: float) -> float:
        """Apply the simple vadose-zone transfer function used in the tests."""
        if hru_id not in self.recharge_delay_state:
            self.recharge_delay_state[hru_id] = {"R_prev": 0.0, "days": 0}

        state = self.recharge_delay_state[hru_id]
        weight_current = delay_days / (delay_days + 1)
        weight_previous = 1 / (delay_days + 1)
        recharge_current = weight_current * dp_mm + weight_previous * state["R_prev"]
        state["R_prev"] = recharge_current
        state["days"] += 1
        return recharge_current

    def swat_unsatisfied_et_to_modflow(
        self,
        unsatisfied_et_hru: Dict[int, float],
        geo_map: Dict[int, List[Tuple[int, float]]],
        extinction_depth_m: float = 3.0,
        head_dict: Dict[int, float] = None,
        cell_elevation_m: Dict[int, float] = None,
        hru_areas_km2: Dict[int, float] = None,
    ) -> Dict[int, float]:
        """Translate SWAT+ unsatisfied ET to MODFLOW ET extraction."""
        cell_et = defaultdict(float)

        for hru_id, et_mm in unsatisfied_et_hru.items():
            if hru_id not in geo_map or et_mm <= 0:
                continue

            hru_area_km2 = hru_areas_km2.get(hru_id, 1.0) if hru_areas_km2 else 1.0

            for cell_id, fraction in self._iter_connections(geo_map[hru_id]):
                if head_dict and cell_elevation_m:
                    water_table_m = head_dict.get(cell_id, -9999)
                    land_surface_m = cell_elevation_m.get(cell_id, 0)
                    depth_to_wt = land_surface_m - water_table_m
                    if depth_to_wt < 0 or depth_to_wt > extinction_depth_m:
                        continue

                cell_et[cell_id] += et_mm * fraction * hru_area_km2 * 1000.0

        return dict(cell_et)

    def swat_irrigation_to_modflow(
        self,
        irrigation_demand: Dict[int, float],
        geo_map: Dict[int, List[Tuple[int, float]]],
        hru_areas_km2: Dict[int, float] = None,
    ) -> Dict[int, float]:
        """Translate SWAT+ irrigation demand to MODFLOW pumping volume."""
        cell_pumping = defaultdict(float)

        for hru_id, demand_mm in irrigation_demand.items():
            if hru_id not in geo_map or demand_mm <= 0:
                continue

            hru_area_km2 = hru_areas_km2.get(hru_id, 1.0) if hru_areas_km2 else 1.0
            demand_m3_day = demand_mm * hru_area_km2 * 1000.0

            connections = list(self._iter_connections(geo_map[hru_id]))
            if not connections:
                continue

            total_weight = sum(weight for _, weight in connections)
            if total_weight <= 0:
                total_weight = float(len(connections))

            for cell_id, weight in connections:
                cell_pumping[cell_id] -= demand_m3_day * (weight / total_weight)

        return dict(cell_pumping)

    def translate_channel_stage(
        self,
        channel_stage: Dict[int, float],
        geo_map: Dict[int, List[Tuple[int, float]]],
        channel_bed_elevation: Dict[int, float] = None,
    ) -> Dict[int, float]:
        """Translate SWAT+ channel stage to a MODFLOW channel-stage map."""
        cell_stage = {}

        for channel_id, stage in channel_stage.items():
            if channel_id not in geo_map:
                continue

            if channel_bed_elevation and channel_id in channel_bed_elevation:
                bed_elev = channel_bed_elevation[channel_id]
                if stage < bed_elev:
                    self.logger.warning(
                        f"Channel {channel_id} stage {stage:.2f} < bed {bed_elev:.2f}; adjusting to bed elevation"
                    )
                    stage = bed_elev

            for cell_id, _ in self._iter_connections(geo_map[channel_id]):
                cell_stage[cell_id] = stage

        return cell_stage

    def modflow_channel_exchange_to_swat(
        self,
        channel_exchange: Dict[int, float],
        geo_map: Dict[int, List[Tuple[int, float]]],
    ) -> Dict[int, float]:
        """Aggregate MODFLOW channel exchange back to SWAT+ channel IDs."""
        channel_baseflow = defaultdict(float)
        cell_to_channel = defaultdict(list)

        for channel_id, connections in geo_map.items():
            for cell_id, _ in self._iter_connections(connections):
                cell_to_channel[cell_id].append(channel_id)

        for cell_id, exchange in channel_exchange.items():
            channels = cell_to_channel.get(cell_id, [])
            if not channels:
                continue

            per_channel = exchange / len(channels)
            for channel_id in channels:
                channel_baseflow[channel_id] += per_channel

        return dict(channel_baseflow)

    def extract_soil_saturation(
        self,
        cell_heads: Dict[int, float],
        geo_map: Dict[int, List[Tuple[int, float]]],
        cell_elevation_m: Dict[int, float] = None,
        cell_specific_yield: Dict[int, float] = None,
        soil_thickness_m: float = 2.0,
    ) -> Dict[int, float]:
        """Extract an equivalent soil-water depth from shallow groundwater."""
        hru_saturation = defaultdict(float)

        for hru_id, connections in geo_map.items():
            for cell_id, fraction in self._iter_connections(connections):
                if cell_id not in cell_heads:
                    continue

                head = cell_heads[cell_id]
                if cell_elevation_m and cell_id in cell_elevation_m:
                    depth_to_wt = cell_elevation_m[cell_id] - head
                else:
                    depth_to_wt = 0.0

                if depth_to_wt < soil_thickness_m:
                    sat_thickness = max(0.0, soil_thickness_m - depth_to_wt)
                    specific_yield = (cell_specific_yield or {}).get(cell_id, 0.15)
                    hru_saturation[hru_id] += sat_thickness * 1000.0 * specific_yield * fraction

        return dict(hru_saturation)

    def compute_soil_transfer(
        self,
        cell_heads: Dict[int, float],
        geo_map: Dict[int, List[Tuple[int, float]]],
        cell_elevation_m: Dict[int, float],
        cell_specific_yield: Dict[int, float],
        cell_areas_m2: Dict[int, float],
        soil_bottom_m: Dict[int, float],
    ) -> Dict[int, float]:
        """Compute groundwater volume to transfer to the SWAT+ soil profile."""
        hru_transfer_m3 = defaultdict(float)

        for hru_id, connections in geo_map.items():
            for cell_id, fraction in self._iter_connections(connections):
                if cell_id not in cell_heads:
                    continue

                head = cell_heads[cell_id]
                soil_base = soil_bottom_m.get(cell_id)
                if soil_base is None:
                    land_elev = cell_elevation_m.get(cell_id)
                    if land_elev is None:
                        continue
                    soil_base = land_elev - 2.0

                saturated_depth_m = head - soil_base
                if saturated_depth_m <= 0:
                    continue

                cell_area_m2 = cell_areas_m2.get(cell_id, 0.0)
                specific_yield = cell_specific_yield.get(cell_id, 0.15)
                hru_transfer_m3[hru_id] += saturated_depth_m * (cell_area_m2 * fraction) * specific_yield

        return dict(hru_transfer_m3)

    def translate_canal_stage(
        self,
        canal_stage: Dict[int, float],
        geo_map: Dict[int, List[Tuple[int, float]]],
        canal_bed_conductivity: Dict[int, float] = None,
        canal_width_m: Dict[int, float] = None,
        canal_bed_thickness_m: Dict[int, float] = None,
        canal_bed_elevation: Dict[int, float] = None,
    ) -> Dict[int, Dict[str, float]]:
        """Translate canal stages into MODFLOW RIV-style records."""
        river_data: Dict[int, Dict[str, float]] = {}

        for canal_id, stage in canal_stage.items():
            if canal_id not in geo_map:
                continue

            if canal_bed_elevation and canal_id in canal_bed_elevation:
                stage = max(stage, canal_bed_elevation[canal_id])

            bed_conductivity = (canal_bed_conductivity or {}).get(canal_id, 1.0)
            width_m = (canal_width_m or {}).get(canal_id, 1.0)
            bed_thickness_m = (canal_bed_thickness_m or {}).get(canal_id, 1.0)
            conductance_base = bed_conductivity * width_m / max(bed_thickness_m, 1e-6)

            for cell_id, length_m in self._iter_connections(geo_map[canal_id]):
                river_data[cell_id] = {
                    "stage": stage,
                    "conductance": conductance_base * length_m,
                    "rbot": stage - max(bed_thickness_m, 1.0),
                }

        return river_data

    def modflow_canal_exchange_to_swat(
        self,
        cell_exchanges: Dict[int, float],
        geo_map: Dict[int, List[Tuple[int, float]]],
    ) -> Dict[int, float]:
        """Aggregate MODFLOW canal exchanges back to SWAT+ canal IDs."""
        canal_baseflow = defaultdict(float)
        cell_to_canal = defaultdict(list)

        for canal_id, connections in geo_map.items():
            for cell_id, _ in self._iter_connections(connections):
                cell_to_canal[cell_id].append(canal_id)

        for cell_id, exchange in cell_exchanges.items():
            canals = cell_to_canal.get(cell_id, [])
            if not canals:
                continue

            per_canal = exchange / len(canals)
            for canal_id in canals:
                canal_baseflow[canal_id] += per_canal

        return dict(canal_baseflow)

    def translate_reservoir_stage(
        self,
        reservoir_stage: Dict[int, float],
        geo_map: Dict[int, List[Tuple[int, float]]],
        reservoir_bed_conductivity: Dict[int, float] = None,
        reservoir_width_m: Dict[int, float] = None,
        reservoir_bed_thickness_m: Dict[int, float] = None,
        reservoir_bed_elevation: Dict[int, float] = None,
    ) -> Dict[int, Dict[str, float]]:
        """Translate reservoir stages into MODFLOW RIV-style records."""
        river_data: Dict[int, Dict[str, float]] = {}

        for reservoir_id, stage in reservoir_stage.items():
            if reservoir_id not in geo_map:
                continue

            if reservoir_bed_elevation and reservoir_id in reservoir_bed_elevation:
                stage = max(stage, reservoir_bed_elevation[reservoir_id])

            bed_conductivity = (reservoir_bed_conductivity or {}).get(reservoir_id, 1.0)
            width_m = (reservoir_width_m or {}).get(reservoir_id, 1.0)
            bed_thickness_m = (reservoir_bed_thickness_m or {}).get(reservoir_id, 1.0)
            conductance_base = bed_conductivity * width_m / max(bed_thickness_m, 1e-6)

            for cell_id, weight in self._iter_connections(geo_map[reservoir_id]):
                river_data[cell_id] = {
                    "stage": stage,
                    "conductance": conductance_base * weight,
                    "rbot": stage - max(bed_thickness_m, 1.0),
                }

        return river_data

    def modflow_reservoir_exchange_to_swat(
        self,
        cell_exchanges: Dict[int, float],
        geo_map: Dict[int, List[Tuple[int, float]]],
    ) -> Dict[int, float]:
        """Aggregate MODFLOW reservoir exchanges back to SWAT+ reservoirs."""
        reservoir_baseflow = defaultdict(float)
        cell_to_reservoir = defaultdict(list)

        for reservoir_id, connections in geo_map.items():
            for cell_id, _ in self._iter_connections(connections):
                cell_to_reservoir[cell_id].append(reservoir_id)

        for cell_id, exchange in cell_exchanges.items():
            reservoirs = cell_to_reservoir.get(cell_id, [])
            if not reservoirs:
                continue

            per_reservoir = exchange / len(reservoirs)
            for reservoir_id in reservoirs:
                reservoir_baseflow[reservoir_id] += per_reservoir

        return dict(reservoir_baseflow)

    def compute_demand_driven_pumping(
        self,
        irrigation_demand_mm: Dict[int, float],
        geo_map: Dict[int, List[Tuple[int, float]]],
        hru_areas_km2: Dict[int, float] = None,
        cell_heads: Dict[int, float] = None,
        cell_bottom_m: Dict[int, float] = None,
        cell_specific_yield: Dict[int, float] = None,
        cell_areas_m2: Dict[int, float] = None,
    ) -> Dict[int, float]:
        """Limit irrigation pumping by available groundwater in each cell."""
        cell_pumping = defaultdict(float)

        for hru_id, demand_mm in irrigation_demand_mm.items():
            if hru_id not in geo_map or demand_mm <= 0:
                continue

            hru_area_km2 = hru_areas_km2.get(hru_id, 1.0) if hru_areas_km2 else 1.0
            demand_m3_day = demand_mm * hru_area_km2 * 1000.0
            connections = list(self._iter_connections(geo_map[hru_id]))
            if not connections:
                continue

            total_weight = sum(weight for _, weight in connections)
            if total_weight <= 0:
                total_weight = float(len(connections))

            for cell_id, weight in connections:
                desired = demand_m3_day * (weight / total_weight)
                available = desired

                if (
                    cell_heads is not None
                    and cell_bottom_m is not None
                    and cell_specific_yield is not None
                    and cell_areas_m2 is not None
                    and cell_id in cell_heads
                    and cell_id in cell_bottom_m
                    and cell_id in cell_specific_yield
                    and cell_id in cell_areas_m2
                ):
                    saturated_depth_m = max(0.0, cell_heads[cell_id] - cell_bottom_m[cell_id])
                    available = saturated_depth_m * cell_areas_m2[cell_id] * cell_specific_yield[cell_id]

                pumped = min(desired, available)
                if pumped > 0:
                    cell_pumping[cell_id] -= pumped

        return dict(cell_pumping)