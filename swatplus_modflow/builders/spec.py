"""Versioned build specification for ``swatplus-modflow build-modflow``.

A build spec describes how to turn GIS layers into a MODFLOW 6 simulation that
the daily engine can run. It is separate from the run project file: the build
is done once (or whenever the layers change), the project file is edited
for every run. Relative paths resolve against the spec's folder; unknown keys
are rejected; ``build_version`` is bumped on incompatible changes.

Physical parameters (conductivities, storage, streambed properties, ...) have no
built-in values: each is a required field, so a model never silently inherits
numbers from somewhere else. The numerical safety margins that do have defaults
are named in their field descriptions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

BUILD_VERSION = 1


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _res(value, info: ValidationInfo):
    if value is None:
        return None
    base = (info.context or {}).get("base_dir")
    p = Path(value).expanduser()
    return (Path(base) / p).resolve() if (not p.is_absolute() and base is not None) else p


class VectorRef(_S):
    path: Path
    layer: Optional[str] = Field(None, description="Layer name inside a multi-layer file (e.g. a GeoPackage).")
    _p = field_validator("path", mode="after")(_res)


class RasterRef(_S):
    path: Path
    resampling: Literal["average", "bilinear", "nearest", "cubic", "min", "max", "med"] = Field(
        "average", description="How raster cells are aggregated onto the model grid.")
    _p = field_validator("path", mode="after")(_res)


class WhereClause(_S):
    field: str
    equals: object


class OutputSpec(_S):
    directory: Path = Field(description="Folder that receives the MODFLOW 6 simulation and the build report.")
    model_name: str = Field("gwf", pattern=r"^[A-Za-z0-9_]{1,16}$")
    write_grid_layer: bool = Field(True, description="Also write grid_cells.gpkg (layer 'grid': row, col, active) "
                                                     "for GIS front ends and build-connections --grid-layer.")
    _p = field_validator("directory", mode="after")(_res)


class DomainSpec(_S):
    polygons: VectorRef
    rule: Literal["overlap", "center"] = Field(
        "overlap", description="'overlap': a cell is active when a polygon overlaps it over a positive area "
                               "(cells that only touch the boundary are inactive); 'center': when its centre "
                               "is inside a polygon.")


class TopSpec(_S):
    dem: RasterRef


class LayerSpec(_S):
    name: str
    thickness_m: Optional[float] = Field(None, gt=0, description="Constant thickness below the layer above.")
    bottom: Optional[RasterRef] = Field(None, description="Raster of the layer bottom elevation.")
    thickness: Optional[RasterRef] = Field(None, description="Raster of the layer thickness.")
    convertible: bool = Field(True, description="Cells convert between confined and unconfined (ICELLTYPE 1).")

    @model_validator(mode="after")
    def _one(self):
        n = sum(x is not None for x in (self.thickness_m, self.bottom, self.thickness))
        if n != 1:
            raise ValueError(f"layer '{self.name}': give exactly one of thickness_m, bottom, thickness")
        return self


class DefaultProps(_S):
    k_m_per_day: float = Field(gt=0, description="Horizontal hydraulic conductivity where no zone applies.")
    k33_over_k: float = Field(gt=0, le=1.0, description="Vertical / horizontal conductivity ratio.")
    sy: float = Field(gt=0, lt=1)
    ss_per_m: float = Field(ge=0)


class ZoneSpec(_S):
    name: str = Field(min_length=1)
    polygons: VectorRef
    where: Optional[WhereClause] = None
    priority: int = Field(description="Where zones overlap, the zone with the LARGER priority wins. "
                                      "Priorities must be unique.")
    layers: Optional[list[int]] = Field(None, description="1-based layers the zone applies to (default: all).")
    k_m_per_day: Optional[float] = Field(None, gt=0)
    k33_over_k: Optional[float] = Field(None, gt=0, le=1.0)
    sy: Optional[float] = Field(None, gt=0, lt=1)
    ss_per_m: Optional[float] = Field(None, ge=0)

    @model_validator(mode="after")
    def _sets_something(self):
        if all(v is None for v in (self.k_m_per_day, self.k33_over_k, self.sy, self.ss_per_m)):
            raise ValueError(f"zone '{self.name}' sets no property")
        return self


class ZoneRules(_S):
    max_zone_share_percent: float = Field(
        60.0, gt=0, le=100, description="A zone that replaces other zones and covers more than this share of "
                                        "a layer's active cells is reported (see on_dominant). Default 60 is a "
                                        "screening threshold, not a physical value.")
    on_dominant: Literal["error", "warn"] = "error"


class PropertiesSpec(_S):
    default: DefaultProps
    zones: list[ZoneSpec] = Field(default_factory=list)
    rules: ZoneRules = Field(default_factory=ZoneRules)

    @model_validator(mode="after")
    def _unique(self):
        names = [z.name for z in self.zones]
        if len(set(names)) != len(names):
            raise ValueError("zone names must be unique")
        pr = [z.priority for z in self.zones]
        if len(set(pr)) != len(pr):
            raise ValueError("zone priorities must be unique, so which zone wins an overlap is never implicit")
        return self


class TopologySpec(_S):
    chandeg_con: Optional[Path] = Field(None, description="SWAT+ chandeg.con (outflow links; reservoirs are followed).")
    table: Optional[Path] = Field(None, description="CSV with channel_id, downstream_id (0 = outlet).")
    _p = field_validator("chandeg_con", "table", mode="after")(_res)

    @model_validator(mode="after")
    def _one(self):
        if (self.chandeg_con is None) == (self.table is None):
            raise ValueError("give exactly one of chandeg_con, table")
        return self


class SfrSpec(_S):
    channel_cells: Path = Field(description="channel_cells.csv (docs/DATA_CONTRACTS.md section 4).")
    topology: TopologySpec
    incision_m: float = Field(ge=0, description="Streambed top = cell top - incision (before monotonic enforcement).")
    bed_thickness_m: float = Field(gt=0)
    bed_k_m_per_day: float = Field(gt=0)
    mannings_n: float = Field(gt=0)
    width_m: float = Field(gt=0, description="Constant channel width; per-channel widths may come from channel_table.")
    channel_table: Optional[Path] = Field(None, description="Optional CSV with channel_id and width_m.")
    min_slope: float = Field(gt=0, description="Floor of the reach gradient (m/m) computed from the bed profile.")
    bed_clearance_m: float = Field(0.01, ge=0, description="Streambed bottom is kept at least this far above the "
                                                          "cell bottom (numerical margin).")
    _p = field_validator("channel_cells", "channel_table", mode="after")(_res)


class EvtSpec(_S):
    rate_m_per_day: float = Field(ge=0, description="Maximum ET rate at the surface.")
    extinction_depth_m: float = Field(gt=0)
    surface_offset_m: float = Field(0.0, description="ET surface = cell top + offset.")


class BoundarySpec(_S):
    name: str
    type: Literal["chd", "drn"]
    features: VectorRef
    where: Optional[WhereClause] = None
    layer: int = Field(1, ge=1)
    line_buffer_cells: bool = Field(True, description="Lines select every cell they touch; polygons use cell centres.")
    head_below_top_m: Optional[float] = Field(None, description="CHD head / DRN elevation = cell top - this.")
    conductance_m2_per_day: Optional[float] = Field(None, gt=0, description="DRN conductance.")

    @model_validator(mode="after")
    def _need(self):
        if self.head_below_top_m is None:
            raise ValueError(f"boundary '{self.name}': head_below_top_m is required")
        if self.type == "drn" and self.conductance_m2_per_day is None:
            raise ValueError(f"boundary '{self.name}': drains need conductance_m2_per_day")
        return self


class InitialHeads(_S):
    mode: Literal["below_top", "constant", "raster"] = "below_top"
    depth_m: Optional[float] = Field(None, ge=0)
    head_m: Optional[float] = None
    raster: Optional[RasterRef] = None

    @model_validator(mode="after")
    def _need(self):
        need = {"below_top": self.depth_m, "constant": self.head_m, "raster": self.raster}[self.mode]
        if need is None:
            raise ValueError(f"initial_heads.mode '{self.mode}' needs "
                             f"{ {'below_top': 'depth_m', 'constant': 'head_m', 'raster': 'raster'}[self.mode] }")
        return self


class BuildSpec(_S):
    build_version: Literal[1]
    name: str = Field(min_length=1)
    grid: Path = Field(description="grid.json (docs/DATA_CONTRACTS.md section 2).")
    output: OutputSpec
    domain: DomainSpec
    top: TopSpec
    layers: list[LayerSpec] = Field(min_length=1)
    min_thickness_m: float = Field(gt=0, description="Cells thinner than this are handled per thin_cells.")
    thin_cells: Literal["error", "enforce"] = "error"
    properties: PropertiesSpec
    initial_heads: InitialHeads = Field(default_factory=lambda: InitialHeads(mode="below_top", depth_m=0.0))
    sfr: Optional[SfrSpec] = None
    evt: Optional[EvtSpec] = None
    boundaries: list[BoundarySpec] = Field(default_factory=list)
    _p = field_validator("grid", mode="after")(_res)

    @model_validator(mode="after")
    def _checks(self):
        names = [l.name for l in self.layers]
        if len(set(names)) != len(names):
            raise ValueError("layer names must be unique")
        nl = len(self.layers)
        for z in self.properties.zones:
            for l in z.layers or []:
                if not 1 <= l <= nl:
                    raise ValueError(f"zone '{z.name}': layer {l} outside 1..{nl}")
        for b in self.boundaries:
            if b.layer > nl:
                raise ValueError(f"boundary '{b.name}': layer {b.layer} outside 1..{nl}")
        return self


def load_spec(path: Path | str) -> BuildSpec:
    path = Path(path).expanduser().resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: build spec must be a mapping at the top level")
    if data.get("build_version") != BUILD_VERSION:
        raise ValueError(f"{path}: build_version {data.get('build_version')!r} is not supported "
                         f"(expected {BUILD_VERSION})")
    return BuildSpec.model_validate(data, context={"base_dir": path.parent})
