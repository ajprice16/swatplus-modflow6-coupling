"""MODFLOW grid description (``grid.json``) and cell arithmetic.

Version 1 grids are uniform and unrotated. ``xorigin``/``yorigin`` are the
lower-left corner, row 1 is the northernmost row and column 1 the westernmost,
all indices are 1-based (see docs/DATA_CONTRACTS.md, section 2).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

GRID_FORMAT = "swatplus-modflow-grid/1"
_CRS_RE = re.compile(r"^[A-Za-z]+:[0-9A-Za-z.]+$")
MAX_CELLS = 5_000_000

Point = Tuple[float, float]


class GridError(ValueError):
    """Raised for an invalid grid definition."""


@dataclass(frozen=True)
class GridSpec:
    crs: str
    xorigin: float
    yorigin: float
    delr: float
    delc: float
    nrow: int
    ncol: int
    angrot: float = 0.0

    def __post_init__(self):
        if not _CRS_RE.match(self.crs or ""):
            raise GridError(f"crs must look like 'AUTHORITY:CODE', got {self.crs!r}")
        if self.angrot != 0.0:
            raise GridError("only unrotated grids (angrot == 0) are supported in format version 1")
        if not (self.delr > 0 and self.delc > 0):
            raise GridError(f"cell sizes must be positive (delr={self.delr}, delc={self.delc})")
        if int(self.nrow) != self.nrow or int(self.ncol) != self.ncol or self.nrow < 1 or self.ncol < 1:
            raise GridError(f"nrow and ncol must be positive integers (nrow={self.nrow}, ncol={self.ncol})")
        for name in ("xorigin", "yorigin", "delr", "delc"):
            if not math.isfinite(getattr(self, name)):
                raise GridError(f"{name} must be finite")

    # -- geometry ---------------------------------------------------------
    @property
    def ytop(self) -> float:
        return self.yorigin + self.nrow * self.delc

    @property
    def xmax(self) -> float:
        return self.xorigin + self.ncol * self.delr

    @property
    def ncells(self) -> int:
        return self.nrow * self.ncol

    @property
    def extent(self) -> Tuple[float, float, float, float]:
        """(xmin, ymin, xmax, ymax)."""
        return (self.xorigin, self.yorigin, self.xmax, self.ytop)

    def cell_bounds(self, row: int, col: int) -> Tuple[float, float, float, float]:
        """(xmin, ymin, xmax, ymax) of the 1-based cell ``(row, col)``."""
        if not (1 <= row <= self.nrow and 1 <= col <= self.ncol):
            raise GridError(f"cell ({row}, {col}) outside grid {self.nrow} x {self.ncol}")
        xmin = self.xorigin + (col - 1) * self.delr
        ymax = self.ytop - (row - 1) * self.delc
        return (xmin, ymax - self.delc, xmin + self.delr, ymax)

    def locate(self, x: float, y: float) -> Optional[Tuple[int, int]]:
        """Cell ``(row, col)`` containing a point, or None outside the grid.

        Cell intervals are half open, ``[min, max)``, except that the grid's
        outer east and south edges belong to the last column and row. A point
        exactly on an interior boundary therefore belongs to the cell to its
        east / south.
        """
        if x < self.xorigin or x > self.xmax or y < self.yorigin or y > self.ytop:
            return None
        col = min(int(math.floor((x - self.xorigin) / self.delr)) + 1, self.ncol)
        row = min(int(math.floor((self.ytop - y) / self.delc)) + 1, self.nrow)
        return row, col

    def cell_range(self, xmin: float, ymin: float, xmax: float, ymax: float
                   ) -> Optional[Tuple[int, int, int, int]]:
        """Inclusive ``(row0, row1, col0, col1)`` of cells overlapping a bbox, clamped to the grid."""
        gx0, gy0, gx1, gy1 = self.extent
        if xmax < gx0 or xmin > gx1 or ymax < gy0 or ymin > gy1:
            return None
        col0 = max(1, int(math.floor((xmin - self.xorigin) / self.delr)) + 1)
        col1 = min(self.ncol, int(math.floor((xmax - self.xorigin) / self.delr)) + 1)
        row0 = max(1, int(math.floor((self.ytop - ymax) / self.delc)) + 1)
        row1 = min(self.nrow, int(math.floor((self.ytop - ymin) / self.delc)) + 1)
        return row0, row1, col0, col1

    # -- (de)serialisation ------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "format": GRID_FORMAT,
            "crs": self.crs,
            "xorigin": float(self.xorigin),
            "yorigin": float(self.yorigin),
            "angrot": 0.0,
            "delr": float(self.delr),
            "delc": float(self.delc),
            "nrow": int(self.nrow),
            "ncol": int(self.ncol),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GridSpec":
        if not isinstance(data, dict):
            raise GridError("grid.json must hold a JSON object")
        fmt = data.get("format")
        if not isinstance(fmt, str) or fmt.split("/")[0] != GRID_FORMAT.split("/")[0]:
            raise GridError(f"unrecognised grid format {fmt!r}")
        if fmt.split("/")[1:] != GRID_FORMAT.split("/")[1:]:
            raise GridError(f"unsupported grid format version {fmt!r} (this plugin reads {GRID_FORMAT})")
        missing = [k for k in ("crs", "xorigin", "yorigin", "delr", "delc", "nrow", "ncol") if k not in data]
        if missing:
            raise GridError(f"grid.json is missing {', '.join(missing)}")
        return cls(crs=str(data["crs"]), xorigin=float(data["xorigin"]), yorigin=float(data["yorigin"]),
                   delr=float(data["delr"]), delc=float(data["delc"]),
                   nrow=int(data["nrow"]), ncol=int(data["ncol"]), angrot=float(data.get("angrot", 0.0)))


def write_grid_json(spec: GridSpec, path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(spec.to_dict(), fh, indent=2)
        fh.write("\n")


def read_grid_json(path) -> GridSpec:
    with open(path, "r", encoding="utf-8") as fh:
        return GridSpec.from_dict(json.load(fh))


def grid_from_extent(crs: str, xmin: float, ymin: float, xmax: float, ymax: float,
                     cell_size: float, buffer: float = 0.0, snap: bool = True) -> GridSpec:
    """Smallest uniform grid with square cells that covers a bbox grown by ``buffer``.

    With ``snap`` the lower-left origin is rounded down to a multiple of the
    cell size, so grids built for different domains with the same cell size
    share cell edges. Without it the origin is exactly (xmin - buffer,
    ymin - buffer).
    """
    if not (cell_size > 0 and math.isfinite(cell_size)):
        raise GridError(f"cell size must be positive, got {cell_size}")
    if buffer < 0:
        raise GridError(f"buffer must not be negative, got {buffer}")
    if not (xmax > xmin and ymax > ymin):
        raise GridError("domain extent has zero width or height")
    x0, y0, x1, y1 = xmin - buffer, ymin - buffer, xmax + buffer, ymax + buffer
    if snap:
        x0 = math.floor(x0 / cell_size) * cell_size
        y0 = math.floor(y0 / cell_size) * cell_size
    ncol = int(math.ceil((x1 - x0) / cell_size - 1e-9))
    nrow = int(math.ceil((y1 - y0) / cell_size - 1e-9))
    if ncol * nrow > MAX_CELLS:
        raise GridError(f"{nrow} x {ncol} = {nrow * ncol} cells exceeds the limit of {MAX_CELLS}; use a larger cell size")
    return GridSpec(crs=crs, xorigin=x0, yorigin=y0, delr=cell_size, delc=cell_size,
                    nrow=max(nrow, 1), ncol=max(ncol, 1))


# -- polyline splitting ---------------------------------------------------
@dataclass
class Piece:
    """A run of a polyline inside one cell (``row``/``col`` None: outside the grid)."""
    row: Optional[int]
    col: Optional[int]
    length: float


def _segment_params(grid: GridSpec, x0: float, y0: float, x1: float, y1: float) -> List[float]:
    ts = {0.0, 1.0}
    dx, dy = x1 - x0, y1 - y0
    if dx != 0.0:
        lo, hi = sorted((x0, x1))
        k0 = max(0, int(math.ceil((lo - grid.xorigin) / grid.delr)))
        k1 = min(grid.ncol, int(math.floor((hi - grid.xorigin) / grid.delr)))
        for k in range(k0, k1 + 1):
            t = (grid.xorigin + k * grid.delr - x0) / dx
            if 0.0 < t < 1.0:
                ts.add(t)
    if dy != 0.0:
        lo, hi = sorted((y0, y1))
        k0 = max(0, int(math.ceil((grid.ytop - hi) / grid.delc)))
        k1 = min(grid.nrow, int(math.floor((grid.ytop - lo) / grid.delc)))
        for k in range(k0, k1 + 1):
            t = (grid.ytop - k * grid.delc - y0) / dy
            if 0.0 < t < 1.0:
                ts.add(t)
    return sorted(ts)


def split_polyline(points: Sequence[Point], grid: GridSpec) -> List[Piece]:
    """Cut a polyline at cell boundaries, in travel order.

    Consecutive sub-segments in the same cell are merged into one ``Piece``;
    a line that leaves a cell and re-enters it yields two pieces. Parts of
    the line outside the grid are returned with ``row = col = None`` so that
    the caller can account for them. The sum of piece lengths equals the
    planar length of the polyline.
    """
    pieces: List[Piece] = []
    for (x0, y0), (x1, y1) in zip(points[:-1], points[1:]):
        seg_len = math.hypot(x1 - x0, y1 - y0)
        if seg_len == 0.0:
            continue
        ts = _segment_params(grid, x0, y0, x1, y1)
        for ta, tb in zip(ts[:-1], ts[1:]):
            length = (tb - ta) * seg_len
            if length <= 0.0:
                continue
            tm = 0.5 * (ta + tb)
            cell = grid.locate(x0 + tm * (x1 - x0), y0 + tm * (y1 - y0))
            row, col = cell if cell else (None, None)
            if pieces and pieces[-1].row == row and pieces[-1].col == col:
                pieces[-1].length += length
            else:
                pieces.append(Piece(row, col, length))
    return pieces


def polyline_length(points: Sequence[Point]) -> float:
    return sum(math.hypot(x1 - x0, y1 - y0) for (x0, y0), (x1, y1) in zip(points[:-1], points[1:]))
