"""
Main SWAT+ and MODFLOW 6 coupling simulator.

This module orchestrates the daily data exchange between SWAT+ and MODFLOW 6:
SWAT+ outputs are translated to MODFLOW stresses, MODFLOW advances, and
groundwater feedback is translated back to SWAT+ objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from .timestep_manager import DailyTimestepManager
from ..coupling.flux_translator import FluxTranslator
from ..coupling.geographic_mapper import GeographicMapper
from ..utils.config import CouplingConfig
from ..utils.water_budget import WaterBudget, WaterBudgetTracker

try:
    import pandas as pd
except ModuleNotFoundError:  # pragma: no cover - exercised in lean test envs
    pd = None

try:
    from .state_manager import SimulationState
except ModuleNotFoundError:  # pragma: no cover - exercised in lean test envs
    SimulationState = None

try:
    from ..interfaces.modflow_interface import MODFLOWInterface
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    MODFLOWInterface = None

try:
    from ..interfaces.swatplus_interface import SWATPlusInterface
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    SWATPlusInterface = None

logger = logging.getLogger(__name__)


@dataclass
class CouplingSettings:
    """Settings for a SWAT+ - MODFLOW 6 coupled run."""

    modflow_dir: Path
    swatplus_dir: Path
    geo_connections_file: Path
    output_dir: Path = Path("./coupling_output")

    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    timestep_days: int = 1

    recharge_delay_days: float = 10.0
    et_extinction_depth_m: float = 3.0

    modflow_convergence_tol: float = 1e-4
    max_iterations: int = 10000

    save_state_interval_days: int = 30
    save_cell_fluxes: bool = True
    save_daily_summary: bool = True

    modflow_executable: Optional[str | Path] = None
    swatplus_executable: Optional[str | Path] = None
    config_file: Optional[str | Path] = None

    def __post_init__(self) -> None:
        self.modflow_dir = Path(self.modflow_dir)
        self.swatplus_dir = Path(self.swatplus_dir)
        self.geo_connections_file = Path(self.geo_connections_file)
        self.output_dir = Path(self.output_dir)
        self.start_date = _parse_date(self.start_date)
        self.end_date = _parse_date(self.end_date)


def _parse_date(value: str | datetime | None) -> Optional[datetime]:
    """Parse user-facing date values."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.strptime(value, "%Y-%m-%d")


class _SimpleTable:
    """Tiny pandas-like fallback used when pandas is not installed."""

    def __init__(self, records: list[Dict[str, Any]]):
        self.records = list(records)
        self.empty = not self.records

    def __len__(self) -> int:
        return len(self.records)

    def to_csv(self, path: str | Path, index: bool = False) -> None:
        import csv

        path = Path(path)
        fieldnames = sorted({key for row in self.records for key in row.keys()})
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.records)

    def head(self, n: int = 5) -> "_SimpleTable":
        return _SimpleTable(self.records[:n])

    def to_string(self, index: bool = False) -> str:
        return "\n".join(str(row) for row in self.records)


class _FallbackSimulationState:
    """Minimal state tracker for environments without pandas installed."""

    def __init__(self):
        self.dates: list[datetime] = []
        self.modflow_heads: Dict[int, list[float]] = {}

    def initialize(self, start_date: datetime, modflow_heads: Dict[int, float]) -> None:
        self.dates = [start_date]
        self.modflow_heads = {cell_id: [head] for cell_id, head in modflow_heads.items()}

    def update(
        self,
        date: datetime,
        modflow_heads: Dict[int, float],
        recharge: Dict[int, float],
        et: Dict[int, float],
        pumping: Dict[int, float],
        baseflow: Dict[int, float],
    ) -> None:
        self.dates.append(date)
        for cell_id, head in modflow_heads.items():
            self.modflow_heads.setdefault(cell_id, []).append(head)

    def get_heads_dataframe(self):
        records = []
        for idx, date in enumerate(self.dates):
            row = {"date": date}
            for cell_id, heads in self.modflow_heads.items():
                if idx < len(heads):
                    row[f"cell_{cell_id}"] = heads[idx]
            records.append(row)
        return _records_to_table(records)


def _records_to_table(records: list[Dict[str, Any]]):
    """Return a pandas DataFrame when available, otherwise a simple table."""
    if pd is not None:
        return pd.DataFrame(records)
    return _SimpleTable(records)


class SWATPlusMODFLOWCoupler:
    """
    Main orchestrator for SWAT+ and MODFLOW 6 integration.

    The constructor accepts either a ``CouplingSettings`` instance or the
    keyword-style API used by the README examples.
    """

    def __init__(
        self,
        settings: Optional[CouplingSettings] = None,
        *,
        modflow_dir: str | Path | None = None,
        swatplus_dir: str | Path | None = None,
        geo_connections_file: str | Path | None = None,
        output_dir: str | Path | None = None,
        config_file: str | Path | None = None,
        modflow_executable: str | Path | None = None,
        swatplus_executable: str | Path | None = None,
    ):
        if settings is not None and not isinstance(settings, CouplingSettings):
            raise TypeError("settings must be a CouplingSettings instance")

        if settings is None:
            missing = [
                name
                for name, value in {
                    "modflow_dir": modflow_dir,
                    "swatplus_dir": swatplus_dir,
                    "geo_connections_file": geo_connections_file,
                }.items()
                if value is None
            ]
            if missing:
                raise ValueError(f"Missing required arguments: {', '.join(missing)}")

            settings = CouplingSettings(
                modflow_dir=Path(modflow_dir),
                swatplus_dir=Path(swatplus_dir),
                geo_connections_file=Path(geo_connections_file),
                output_dir=Path(output_dir or "./coupling_output"),
                config_file=config_file,
                modflow_executable=modflow_executable,
                swatplus_executable=swatplus_executable,
            )

        self.settings = settings
        self.output_dir = settings.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.config = (
            CouplingConfig.from_yaml(settings.config_file)
            if settings.config_file
            else CouplingConfig()
        )
        self._apply_settings_to_config()

        if SWATPlusInterface is None:
            raise ImportError("SWATPlusInterface dependencies are not available")
        if MODFLOWInterface is None:
            raise ImportError("MODFLOWInterface dependencies are not available")

        self.swatplus = SWATPlusInterface(
            settings.swatplus_dir,
            executable=settings.swatplus_executable,
        )
        self.modflow = MODFLOWInterface(
            settings.modflow_dir,
            executable=settings.modflow_executable,
        )

        self.mapper: Optional[GeographicMapper] = None
        self.translator = FluxTranslator()
        state_cls = SimulationState or _FallbackSimulationState
        self.state = state_cls()
        self.timestep_manager: Optional[DailyTimestepManager] = None
        self.budget_tracker = WaterBudgetTracker()

        self.daily_results: list[Dict[str, Any]] = []
        self.cell_results: list[Dict[str, Any]] = []
        self._last_heads: Dict[int, float] = {}
        self._swatplus_objects: Dict[str, Any] = {}

        logger.info("Initialized SWAT+ - MODFLOW 6 coupler")
        logger.info("  SWAT+ model: %s", settings.swatplus_dir)
        logger.info("  MODFLOW model: %s", settings.modflow_dir)
        logger.info("  Output directory: %s", self.output_dir)

    def _apply_settings_to_config(self) -> None:
        """Let explicit settings override config-file defaults."""
        coupling = self.config.coupling
        coupling.recharge_delay_days = self.settings.recharge_delay_days
        coupling.et_extinction_depth_m = self.settings.et_extinction_depth_m
        coupling.modflow_convergence_tol = self.settings.modflow_convergence_tol
        coupling.max_iterations = self.settings.max_iterations
        coupling.save_state_interval_days = self.settings.save_state_interval_days
        coupling.save_cell_fluxes = self.settings.save_cell_fluxes
        coupling.save_daily_summary = self.settings.save_daily_summary

    def load_geographic_connections(self) -> None:
        """Load GIS connections between SWAT+ objects and MODFLOW cells."""
        self._swatplus_objects = self.swatplus.get_object_info()
        self.mapper = GeographicMapper(
            connections_file=self.settings.geo_connections_file,
            modflow_grid=self.modflow.get_grid_info(),
            swatplus_objects=self._swatplus_objects,
        )
        self.mapper.load_connections()

        logger.info("Loaded %s HRU connections", self.mapper.n_hru_connections)
        logger.info("Loaded %s channel connections", self.mapper.n_channel_connections)
        logger.info("Loaded %s drain connections", self.mapper.n_drain_connections)

    def initialize_models(self, start_date: datetime, end_date: datetime) -> None:
        """Initialize SWAT+, MODFLOW, timestep tracking, and initial state."""
        self.swatplus.initialize(start_date=start_date, end_date=end_date)
        self.modflow.initialize(
            start_date=start_date,
            end_date=end_date,
            convergence_tol=self.config.coupling.modflow_convergence_tol,
            max_iterations=self.config.coupling.max_iterations,
        )

        self.timestep_manager = DailyTimestepManager(
            start_date=start_date,
            end_date=end_date,
            timestep_days=self.settings.timestep_days,
        )

        self._last_heads = self.modflow.get_initial_heads()
        self.state.initialize(start_date=start_date, modflow_heads=self._last_heads)

    def run(
        self,
        start_date: str | datetime | None = None,
        end_date: str | datetime | None = None,
        output_dir: str | Path | None = None,
    ) -> Dict[str, Any]:
        """
        Run the coupled simulation.

        Args:
            start_date: Optional start date. Defaults to settings.start_date.
            end_date: Optional end date. Defaults to settings.end_date.
            output_dir: Optional output directory override.
        """
        if self.mapper is None:
            raise ValueError("Must call load_geographic_connections() before run()")

        start = _parse_date(start_date) or self.settings.start_date
        end = _parse_date(end_date) or self.settings.end_date
        if start is None or end is None:
            raise ValueError("start_date and end_date are required")
        if end < start:
            raise ValueError("end_date must be on or after start_date")

        if output_dir is not None:
            self.output_dir = Path(output_dir)
            self.settings.output_dir = self.output_dir
            self.output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Starting coupled run: %s to %s", start.date(), end.date())
        self.initialize_models(start, end)

        current_date = start
        while current_date <= end:
            self._run_timestep(current_date)
            current_date += timedelta(days=self.settings.timestep_days)

        self._finalize_simulation()
        logger.info("Coupled run completed")
        return {
            "daily_results": self.daily_results,
            "water_budget": self.budget_tracker.get_cumulative_budget(),
            "output_dir": self.output_dir,
        }

    def _run_timestep(self, current_date: datetime) -> None:
        """Execute one coupled daily timestep."""
        if self.mapper is None:
            raise RuntimeError("Geographic connections are not loaded")

        swat_outputs = self.swatplus.advance_one_day(current_date)
        hru_areas_km2 = self._get_hru_areas()

        recharge_hru = swat_outputs.get("recharge", {})
        unsatisfied_et_hru = swat_outputs.get("unsatisfied_et", {})
        channel_stage = swat_outputs.get("channel_stage", {})
        irrigation_demand = swat_outputs.get("irrigation_demand", {})

        recharge_cells = self.translator.swat_recharge_to_modflow(
            recharge_hru=recharge_hru,
            geo_map=self.mapper.hru_to_cells,
            delay_days=self.config.coupling.recharge_delay_days,
            hru_areas_km2=hru_areas_km2,
        )
        et_cells = self.translator.swat_unsatisfied_et_to_modflow(
            unsatisfied_et_hru=unsatisfied_et_hru,
            geo_map=self.mapper.hru_to_cells,
            extinction_depth_m=self.config.coupling.et_extinction_depth_m,
            head_dict=self._last_heads,
            hru_areas_km2=hru_areas_km2,
        )
        pumping_cells = self.translator.swat_irrigation_to_modflow(
            irrigation_demand=irrigation_demand,
            geo_map=self.mapper.hru_to_cells,
            hru_areas_km2=hru_areas_km2,
        )
        channel_stages = self.translator.translate_channel_stage(
            channel_stage=channel_stage,
            geo_map=self.mapper.channel_to_cells,
        )

        modflow_results = self.modflow.advance_one_day(
            recharge_cells=recharge_cells,
            et_cells=et_cells,
            pumping_cells=pumping_cells,
            channel_stages=channel_stages,
            current_date=current_date,
        ) or {}

        heads = modflow_results.get("heads", {})
        channel_exchange = modflow_results.get("channel_exchange", {})
        drain_exchange = modflow_results.get("drain_exchange", {})
        self._last_heads = heads or self._last_heads

        baseflow = self.translator.modflow_channel_exchange_to_swat(
            channel_exchange=channel_exchange,
            geo_map=self.mapper.channel_to_cells,
        )
        soil_saturation = self.translator.extract_soil_saturation(
            cell_heads=self._last_heads,
            geo_map=self.mapper.hru_to_cells,
        )

        self.swatplus.update_groundwater_state(
            baseflow=baseflow,
            soil_saturation=soil_saturation,
            drain_flow=drain_exchange,
        )

        self.state.update(
            date=current_date,
            modflow_heads=self._last_heads,
            recharge=recharge_cells,
            et=et_cells,
            pumping=pumping_cells,
            baseflow=baseflow,
        )
        self._record_daily_results(
            current_date=current_date,
            recharge_cells=recharge_cells,
            et_cells=et_cells,
            pumping_cells=pumping_cells,
            baseflow=baseflow,
            drain_exchange=drain_exchange,
        )

    def _get_hru_areas(self) -> Dict[int, float]:
        """Return HRU areas in km2 from SWAT+ object metadata."""
        hru_properties = self._swatplus_objects.get("hru_properties", {})
        return {
            int(hru_id): float(props.get("area_km2", 1.0))
            for hru_id, props in hru_properties.items()
        }

    def _record_daily_results(
        self,
        current_date: datetime,
        recharge_cells: Dict[int, float],
        et_cells: Dict[int, float],
        pumping_cells: Dict[int, float],
        baseflow: Dict[int, float],
        drain_exchange: Dict[int, float],
    ) -> None:
        """Store daily summaries and water-budget records."""
        recharge_m3 = sum(recharge_cells.values())
        et_m3 = -abs(sum(et_cells.values()))
        pumping_m3 = sum(pumping_cells.values())
        baseflow_m3 = sum(v for v in baseflow.values() if v > 0)
        drain_m3 = sum(abs(v) for v in drain_exchange.values())

        self.daily_results.append(
            {
                "date": current_date,
                "recharge_m3": recharge_m3,
                "unsatisfied_et_m3": et_m3,
                "pumping_m3": pumping_m3,
                "baseflow_m3": baseflow_m3,
                "drain_outflow_m3": drain_m3,
                "n_recharge_cells": len(recharge_cells),
                "n_et_cells": len(et_cells),
                "n_pumping_cells": len(pumping_cells),
            }
        )

        self.budget_tracker.add_daily_budget(
            WaterBudget(
                date=current_date,
                recharge_m3=recharge_m3,
                unsatisfied_et_m3=et_m3,
                channel_discharge_m3=baseflow_m3,
                pumping_m3=pumping_m3,
                drain_outflow_m3=drain_m3,
            )
        )

        if self.config.coupling.save_cell_fluxes:
            self._append_cell_results(current_date, "recharge", recharge_cells)
            self._append_cell_results(current_date, "et", et_cells)
            self._append_cell_results(current_date, "pumping", pumping_cells)

    def _append_cell_results(
        self,
        current_date: datetime,
        flux_type: str,
        values: Dict[int, float],
    ) -> None:
        for cell_id, value in values.items():
            self.cell_results.append(
                {
                    "date": current_date,
                    "flux_type": flux_type,
                    "cell_id": cell_id,
                    "value_m3_day": value,
                }
            )

    def _finalize_simulation(self) -> None:
        """Write configured output tables."""
        if self.config.coupling.save_daily_summary:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.get_daily_flows().to_csv(
                self.output_dir / "daily_flows_summary.csv",
                index=False,
            )

        if self.config.coupling.save_cell_fluxes and self.cell_results:
            _records_to_table(self.cell_results).to_csv(
                self.output_dir / "cell_fluxes.csv",
                index=False,
            )

    def get_daily_flows(self):
        """Return daily coupling flux summaries."""
        return _records_to_table(self.daily_results)

    def get_daily_heads(self):
        """Return MODFLOW head history tracked by the coupler."""
        return self.state.get_heads_dataframe()

    def get_water_balance(self) -> str:
        """Return a concise cumulative water-budget report."""
        cumulative = self.budget_tracker.get_cumulative_budget()
        if not cumulative:
            return "No water budget data available"

        return (
            "WATER BUDGET SUMMARY\n"
            "====================\n"
            f"Days: {len(self.budget_tracker.daily_budgets)}\n"
            f"Recharge: {cumulative['recharge_m3']:.2f} m3\n"
            f"Unsatisfied ET: {cumulative['unsatisfied_et_m3']:.2f} m3\n"
            f"Pumping: {cumulative['pumping_m3']:.2f} m3\n"
            f"Channel discharge: {cumulative['channel_discharge_m3']:.2f} m3\n"
            f"Drain outflow: {cumulative['drain_outflow_m3']:.2f} m3\n"
            f"Cumulative error: {cumulative['cumulative_error_m3']:.2f} m3"
        )
