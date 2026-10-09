"""Model grid (``grid.json``, docs/DATA_CONTRACTS.md section 2) and raster helpers."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

GRID_FORMAT = "swatplus-modflow-grid/1"
_CRS_RE = re.compile(r"^[A-Za-z]+:[0-9A-Za-z.]+$")


class BuildInputError(ValueError):
    """The build spec or one of its inputs is invalid (exit code 1)."""


@dataclass(frozen=True)
class Grid:
    crs: str
    xorigin: float
    yorigin: float          # lower-left corner
    delr: float
    delc: float
    nrow: int
    ncol: int
    angrot: float = 0.0

    @property
    def ytop(self) -> float:
        return self.yorigin + self.nrow * self.delc

    @property
    def transform(self):
        from rasterio.transform import from_origin
        return from_origin(self.xorigin, self.ytop, self.delr, self.delc)

    @property
    def cell_area(self) -> float:
        return self.delr * self.delc

    def centers(self):
        """Cell-centre x (ncol,) and y (nrow,) coordinates; row 0 is the northernmost row."""
        x = self.xorigin + (np.arange(self.ncol) + 0.5) * self.delr
        y = self.ytop - (np.arange(self.nrow) + 0.5) * self.delc
        return x, y

    def cell_of(self, x: np.ndarray, y: np.ndarray):
        """0-based ``(row, col)`` containing each point, and a mask of points inside the grid."""
        col = np.floor((np.asarray(x) - self.xorigin) / self.delr).astype(int)
        row = np.floor((self.ytop - np.asarray(y)) / self.delc).astype(int)
        ok = (row >= 0) & (row < self.nrow) & (col >= 0) & (col < self.ncol)
        return row, col, ok

    def to_json(self) -> dict:
        return {"format": GRID_FORMAT, "crs": self.crs, "xorigin": self.xorigin, "yorigin": self.yorigin,
                "angrot": self.angrot, "delr": self.delr, "delc": self.delc, "nrow": self.nrow,
                "ncol": self.ncol}


def read_grid(path: Path) -> Grid:
    path = Path(path)
    if not path.exists():
        raise BuildInputError(f"grid file not found: {path}")
    try:
        d = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise BuildInputError(f"{path.name}: not valid JSON ({exc})") from None
    fmt = d.get("format", "")
    if not str(fmt).startswith("swatplus-modflow-grid/"):
        raise BuildInputError(f"{path.name}: format must be '{GRID_FORMAT}', got {fmt!r}")
    if str(fmt).split("/")[-1].split(".")[0] != "1":
        raise BuildInputError(f"{path.name}: unsupported grid format version {fmt!r}")
    try:
        g = Grid(crs=str(d["crs"]).upper(), xorigin=float(d["xorigin"]), yorigin=float(d["yorigin"]),
                 delr=float(d["delr"]), delc=float(d["delc"]), nrow=int(d["nrow"]), ncol=int(d["ncol"]),
                 angrot=float(d.get("angrot", 0.0)))
    except KeyError as exc:
        raise BuildInputError(f"{path.name}: missing field {exc}") from None
    if g.angrot != 0.0:
        raise BuildInputError(f"{path.name}: version 1 grids are unrotated (angrot must be 0)")
    if min(g.delr, g.delc) <= 0 or g.nrow < 1 or g.ncol < 1:
        raise BuildInputError(f"{path.name}: delr, delc, nrow, ncol must be positive")
    if not _CRS_RE.match(g.crs):
        raise BuildInputError(f"{path.name}: crs must look like 'AUTHORITY:CODE', got {g.crs!r}")
    return g


def crs_equal(a, b) -> bool:
    """True when two CRS definitions (strings or objects) describe the same CRS."""
    from pyproj import CRS
    try:
        return CRS.from_user_input(a) == CRS.from_user_input(b)
    except Exception:
        return False


def sample_raster_to_grid(path: Path, grid: Grid, resampling: str = "average",
                          band: int = 1) -> np.ndarray:
    """Resample a raster onto the model grid (``(nrow, ncol)``; NaN where the raster has no data)."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    try:
        rs = getattr(Resampling, resampling)
    except AttributeError:
        raise BuildInputError(f"unknown resampling '{resampling}'") from None
    with rasterio.open(path) as src:
        if src.crs is None:
            raise BuildInputError(f"{Path(path).name}: raster has no CRS; refusing to assume one")
        if not crs_equal(src.crs, grid.crs):
            raise BuildInputError(f"{Path(path).name}: raster CRS {src.crs} differs from the grid CRS "
                                  f"{grid.crs}; reproject it first")
        dst = np.full((grid.nrow, grid.ncol), np.nan, dtype=np.float64)
        reproject(source=rasterio.band(src, band), destination=dst, src_transform=src.transform,
                  src_crs=src.crs, src_nodata=src.nodata, dst_transform=grid.transform, dst_crs=grid.crs,
                  dst_nodata=np.nan, resampling=rs)
    return dst


def rasterize_geometries(geoms, grid: Grid, all_touched: bool = False) -> np.ndarray:
    """Boolean ``(nrow, ncol)``: cells whose centre is inside (or, if ``all_touched``, that touch) ``geoms``."""
    from rasterio.features import rasterize
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return np.zeros((grid.nrow, grid.ncol), dtype=bool)
    out = rasterize([(g, 1) for g in geoms], out_shape=(grid.nrow, grid.ncol), transform=grid.transform,
                    fill=0, all_touched=all_touched, dtype="uint8")
    return out.astype(bool)


def read_vector(path: Path, grid: Grid, layer: Optional[str] = None, where: Optional[dict] = None):
    """Read a vector layer, check its CRS against the grid and apply an optional ``field == value`` filter."""
    import geopandas as gpd
    if not Path(path).exists():
        raise BuildInputError(f"vector file not found: {path}")
    gdf = gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
    if gdf.crs is None:
        raise BuildInputError(f"{Path(path).name}: layer has no CRS; refusing to assume one")
    if not crs_equal(gdf.crs, grid.crs):
        raise BuildInputError(f"{Path(path).name}: CRS {gdf.crs} differs from the grid CRS {grid.crs}; "
                              "reproject it first")
    if where:
        f, v = where["field"], where["equals"]
        if f not in gdf.columns:
            raise BuildInputError(f"{Path(path).name}: field '{f}' not found (fields: {list(gdf.columns)})")
        gdf = gdf[gdf[f] == v]
        if gdf.empty:
            raise BuildInputError(f"{Path(path).name}: no feature has {f} == {v!r}")
    return gdf


def overlap_mask(geoms, grid: Grid) -> np.ndarray:
    """Cells that overlap ``geoms`` over a positive area (cells that only touch the boundary are excluded)."""
    import shapely
    from shapely import STRtree
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    cand = rasterize_geometries(geoms, grid, all_touched=True)
    out = np.zeros_like(cand)
    rr, cc = np.where(cand)
    if len(rr) == 0:
        return out
    x0 = grid.xorigin + cc * grid.delr
    y1 = grid.ytop - rr * grid.delc
    boxes = shapely.box(x0, y1 - grid.delc, x0 + grid.delr, y1)
    tree = STRtree(boxes)
    area = np.zeros(len(boxes))
    for g in geoms:
        gi, bi = tree.query(g, predicate="intersects"), None
        if len(gi) == 0:
            continue
        inter = shapely.area(shapely.intersection(g, boxes[gi]))
        np.add.at(area, gi, inter)
    ok = area > 1e-9 * grid.cell_area
    out[rr[ok], cc[ok]] = True
    return out
