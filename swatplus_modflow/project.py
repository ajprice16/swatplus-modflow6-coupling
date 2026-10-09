"""Versioned project configuration for a coupled SWAT+ / MODFLOW 6 run.

A project file is the single contract between the GIS front end (the QGIS
plugin, or a user writing YAML by hand) and the coupling engine. It holds
every path and option a run needs; the engine has no built-in study-area
defaults.

* Relative paths are resolved against the directory containing the
  project file, so a project folder can be moved or shared intact.
* Unknown keys are rejected, so typos fail loudly instead of being ignored.
* The file is YAML. JSON is valid YAML, so a writer without PyYAML (for
  example QGIS's bundled Python) can emit JSON with the standard library.
* ``schema_version`` is bumped on any incompatible change; the loader
  refuses versions it does not know.

Units follow the models: SWAT+ fluxes in mm/day over HRU area, MODFLOW 6
lengths in the model's LENGTH_UNITS (metres assumed by the engine and
checked at run time), volumes in m^3/day.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

SCHEMA_VERSION = 1
_CRS_RE = re.compile(r"^[A-Za-z]+:[0-9A-Za-z.]+$")


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _resolve(value: Optional[Path], info: ValidationInfo) -> Optional[Path]:
    if value is None:
        return None
    base = (info.context or {}).get("base_dir")
    value = Path(value).expanduser()
    if not value.is_absolute() and base is not None:
        value = (Path(base) / value).resolve()
    return value


class SwatPlusSection(_Section):
    txtinout: Path = Field(description="SWAT+ TxtInOut directory (inputs, and outputs once run).")
    executable: Optional[str] = Field(
        None, description="SWAT+ executable path or name on PATH. Required when run_swatplus is true.")
    run_swatplus: bool = Field(
        True, description="Run SWAT+ before MODFLOW. False reuses the daily outputs already in txtinout.")

    _paths = field_validator("txtinout", mode="after")(_resolve)

    @model_validator(mode="after")
    def _exe_needed(self):
        if self.run_swatplus and not self.executable:
            raise ValueError("swatplus.executable is required when swatplus.run_swatplus is true")
        return self


class SolverSection(_Section):
    complexity: Literal["simple", "moderate", "complex"] = "complex"
    newton: bool = Field(True, description="Use the Newton-Raphson formulation (recommended for dry/rewetting cells).")
    under_relaxation: bool = True
    inner_rclose: Optional[float] = Field(
        None, gt=0, description="Residual closure of the linear solver (model volume/time). "
                                "Default: the value implied by 'complexity'.")
    outer_maximum: int = Field(200, gt=0)
    inner_maximum: int = Field(300, gt=0)
    outer_dvclose: float = Field(1.0e-3, gt=0, description="Head-change closure (model length units).")
    inner_dvclose: float = Field(1.0e-4, gt=0)


class ModflowSection(_Section):
    sim_dir: Path = Field(description="MODFLOW 6 simulation directory holding mfsim.nam and the static packages.")
    model_name: Optional[str] = Field(None, description="GWF model name; may be omitted when the simulation has one model.")
    libmf6: Path = Field(description="Path to the libmf6 shared library (.so / .dylib / .dll).")
    initial_heads: Optional[Path] = Field(
        None, description="Binary head file whose last record is used as starting heads. Default: the IC package.")
    solver: SolverSection = Field(default_factory=SolverSection)

    _paths = field_validator("sim_dir", "libmf6", "initial_heads", mode="after")(_resolve)


class ConnectionsSection(_Section):
    hru_cells: Path = Field(description="HRU <-> cell table hru_cells.csv (hru_id, layer, row, col, area_m2, fraction).")
    channel_reaches: Path = Field(description="SWAT+ channel <-> MF6 SFR reach table sfr_reaches.csv "
                                              "(rno, channel_id, seq, layer, row, col, length_m).")
    well_cells: Optional[Path] = Field(
        None, description="Optional pumping table well_cells.csv (layer, row, col, rate_m3d; MODFLOW sign: "
                          "negative = extraction). Added to the run as an extra WEL package.")
    channel_cells: Optional[Path] = Field(
        None, description="Optional channel_cells.csv (the GIS-side channel <-> cell table). Not needed to run; "
                          "when given, validate cross-checks it against sfr_reaches.csv and the run reports "
                          "the channel flow from off-grid channel length that is injected into in-grid "
                          "reaches (daily column runoff_from_offgrid_length_m3).")
    grid: Optional[Path] = Field(
        None, description="Optional grid.json. When given, validate checks it against the MODFLOW "
                          "discretization and the project CRS.")

    _paths = field_validator("hru_cells", "channel_reaches", "well_cells", "channel_cells", "grid",
                             mode="after")(_resolve)


class PeriodSection(_Section):
    start: date
    end: date
    spinup_days: int = Field(0, ge=0, description="Days run before 'start' whose output is discarded.")

    @model_validator(mode="after")
    def _order(self):
        if self.end <= self.start:
            raise ValueError(f"period.end ({self.end}) must be after period.start ({self.start})")
        return self


class TransmissionLossSection(_Section):
    k_mm_per_h: float = Field(gt=0, description="Effective channel-bed hydraulic conductivity.")
    duration_h: float = Field(12.0, gt=0, le=24, description="Hours per day of flow-weighted loss.")


class CouplingSection(_Section):
    mode: Literal["sequential_daily"] = Field(
        "sequential_daily",
        description="SWAT+ runs first; MODFLOW 6 then advances one day at a time through libmf6, "
                    "with SFR runoff and recharge set in memory from the SWAT+ daily output.")
    recharge_update: Literal["daily", "monthly", "monthly_ramped"] = "monthly"
    recharge_delay_days: float = Field(
        0.0, ge=0, description="Vadose-zone delay (SWAT gw_delay form). 0 passes percolation through unchanged.")
    recharge_to_water_table: bool = Field(True, description="Apply recharge to the highest active cell.")
    et_extinction_depth_m: Optional[float] = Field(
        None, gt=0, description="Overrides the EVT extinction depth when set; otherwise the EVT package value is used.")
    pumping_scale: float = Field(1.0, ge=0, description="Multiplier on well rates (0 disables pumping).")
    well_auto_flow_reduce: Optional[float] = Field(
        None, gt=0, le=1, description="AUTO_FLOW_REDUCE fraction for wells added from connections.well_cells "
                                      "(throttle pumping as the cell thins). None = off.")
    evt_monthly_factors: Optional[list[float]] = Field(
        None, description="Optional 12 multipliers (January..December) applied to the EVT package rate. "
                          "No default: the model's own rate is used unchanged when omitted.")
    transmission_loss: Optional[TransmissionLossSection] = None

    @field_validator("evt_monthly_factors")
    @classmethod
    def _twelve(cls, v):
        if v is not None and (len(v) != 12 or any(x < 0 for x in v)):
            raise ValueError("evt_monthly_factors needs 12 non-negative values (January..December)")
        return v


class ChecksSection(_Section):
    enforce_invariants: bool = Field(True, description="Stop the run when a coupling invariant fails.")
    mass_balance_tolerance_percent: float = Field(0.1, gt=0, le=5.0)
    allow_nonconvergence_steps: int = Field(0, ge=0, description="Non-converged days tolerated before stopping.")


class OutputSection(_Section):
    directory: Path = Path("results")
    prefix: str = Field("coupled_", pattern=r"^[A-Za-z0-9_.-]*$")
    save_heads: bool = True
    save_budget: bool = True
    save_daily_summary: bool = True
    save_reach_flows: bool = Field(False, description="Also write per-reach daily outflow and stream-aquifer exchange.")

    _paths = field_validator("directory", mode="after")(_resolve)


class ProjectConfig(_Section):
    schema_version: Literal[1] = Field(description="Project file format version.")
    name: str = Field(min_length=1, description="Free-text project label used in output metadata.")
    crs: str = Field(description="Projected CRS shared by every spatial input, e.g. 'EPSG:<code>'.")
    swatplus: SwatPlusSection
    modflow: ModflowSection
    connections: ConnectionsSection
    period: PeriodSection
    coupling: CouplingSection = Field(default_factory=CouplingSection)
    checks: ChecksSection = Field(default_factory=ChecksSection)
    output: OutputSection = Field(default_factory=OutputSection)

    @field_validator("crs")
    @classmethod
    def _crs_format(cls, v: str) -> str:
        if not _CRS_RE.match(v):
            raise ValueError(f"crs must look like 'AUTHORITY:CODE', got {v!r}")
        return v.upper()

    @model_validator(mode="after")
    def _resolve_defaults(self, info: ValidationInfo):
        # Sections built from defaults never see the validation context, so a
        # default relative path (output.directory) is resolved here instead.
        if not self.output.directory.is_absolute():
            self.output.directory = _resolve(self.output.directory, info)
        return self

    def missing_paths(self) -> list[str]:
        """Return descriptions of required input paths that do not exist."""
        hints = {"connections.channel_reaches":
                 " (written by `swatplus-modflow build-modflow <build spec>`; run the build first)"}
        checks = [
            ("swatplus.txtinout", self.swatplus.txtinout),
            ("modflow.sim_dir", self.modflow.sim_dir),
            ("modflow.libmf6", self.modflow.libmf6),
            ("modflow.initial_heads", self.modflow.initial_heads),
            ("connections.hru_cells", self.connections.hru_cells),
            ("connections.channel_reaches", self.connections.channel_reaches),
            ("connections.well_cells", self.connections.well_cells),
            ("connections.channel_cells", self.connections.channel_cells),
            ("connections.grid", self.connections.grid),
        ]
        return [f"{k}: {p} not found{hints.get(k, '')}" for k, p in checks
                if p is not None and not Path(p).exists()]


def load_project(path: Path | str) -> ProjectConfig:
    """Load and validate a project file; relative paths resolve against its folder."""
    path = Path(path).expanduser().resolve()
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: project file must be a mapping at the top level")
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"{path}: schema_version {version!r} is not supported by this engine "
            f"(expected {SCHEMA_VERSION})")
    return ProjectConfig.model_validate(data, context={"base_dir": path.parent})


def save_project(cfg: ProjectConfig, path: Path | str) -> None:
    """Write a project file (absolute paths; YAML)."""
    data = json.loads(cfg.model_dump_json())
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)


def json_schema() -> dict:
    """JSON Schema of the project file, for documentation and front-end validation."""
    return ProjectConfig.model_json_schema()
