"""Helpers shared by the Processing algorithms (QGIS API only; no Qt-version specifics)."""
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Tuple

from qgis.core import NULL, QgsCoordinateReferenceSystem, QgsGeometry, QgsProcessingException

from ..core.grid import GridError, GridSpec

GROUP_ID = "preparation"
GROUP_NAME = "Coupling preparation"
_INT_RE = re.compile(r"^\s*([+-]?\d+)(?:\.0+)?\s*$")


def is_null(value) -> bool:
    if value is None:
        return True
    try:
        return bool(value == NULL)
    except Exception:  # pragma: no cover - exotic variant types
        return False


def as_int_id(value, what: str, feature_id) -> int:
    """Integer id from an attribute value (int, integral float or digit string)."""
    if is_null(value):
        raise QgsProcessingException(f"{what}: feature {feature_id} has a NULL id")
    if isinstance(value, bool):
        raise QgsProcessingException(f"{what}: feature {feature_id} has a boolean id")
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if math.isfinite(value) and value == int(value):
            return int(value)
        raise QgsProcessingException(f"{what}: feature {feature_id} has a non-integer id {value!r}")
    m = _INT_RE.match(str(value))
    if not m:
        raise QgsProcessingException(f"{what}: feature {feature_id} has a non-integer id {value!r}")
    return int(m.group(1))


def authid_of(crs: QgsCoordinateReferenceSystem, what: str) -> str:
    """'AUTHORITY:CODE' of a projected CRS or an informative exception."""
    if not crs.isValid():
        raise QgsProcessingException(f"{what} has no valid CRS; assign one first")
    if crs.isGeographic():
        raise QgsProcessingException(
            f"{what} uses a geographic CRS ({crs.authid()}); lengths and areas must be in a projected CRS (metres). "
            "Reproject it with the native 'Reproject layer' tool first.")
    authid = crs.authid()
    if not authid or ":" not in authid:
        raise QgsProcessingException(
            f"{what} uses a CRS without an authority code ('{crs.description()}'); the engine needs 'AUTHORITY:CODE'. "
            "Reproject the layer to a standard CRS.")
    return authid


def require_same_crs(authid_a: str, label_a: str, source, label_b: str) -> None:
    other = authid_of(source.sourceCrs(), label_b)
    if other != authid_a:
        raise QgsProcessingException(
            f"{label_b} is in {other} but {label_a} is in {authid_a}. All inputs must share one CRS "
            "(strict: nothing is reprojected silently); reproject with the native 'Reproject layer' tool.")


def require_fields(source, names: List[str], what: str) -> None:
    have = {f.name() for f in source.fields()}
    missing = [n for n in names if n not in have]
    if missing:
        raise QgsProcessingException(f"{what} has no field {', '.join(repr(m) for m in missing)} (found: {', '.join(sorted(have))})")


def grid_spec_from_source(source, crs_authid: str, feedback=None) -> Tuple[GridSpec, Dict[Tuple[int, int], int]]:
    """Reconstruct the uniform grid from a grid polygon layer and verify the layer follows it.

    Requires integer-valued ``row``, ``col`` and ``active`` fields. Returns the GridSpec and a
    mapping ``(row, col) -> active``. Every cell must be a rectangle that sits exactly where
    row 1 = north, col 1 = west dictates; otherwise a QgsProcessingException names the cell.
    """
    require_fields(source, ["row", "col"], "Grid layer")
    has_active = "active" in {f.name() for f in source.fields()}
    cells = []
    for f in source.getFeatures():
        g = f.geometry()
        if g.isNull() or g.isEmpty():
            raise QgsProcessingException(f"Grid layer: feature {f.id()} has no geometry")
        bb = g.boundingBox()
        r, c = as_int_id(f["row"], "Grid layer row", f.id()), as_int_id(f["col"], "Grid layer col", f.id())
        act = as_int_id(f["active"], "Grid layer active", f.id()) if has_active else 1
        cells.append((r, c, act, bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum(), g.area()))
    if not cells:
        raise QgsProcessingException("Grid layer has no features")
    nrow = max(t[0] for t in cells)
    ncol = max(t[1] for t in cells)
    delr = cells[0][5] - cells[0][3]
    delc = cells[0][6] - cells[0][4]
    xorigin = min(t[3] for t in cells)
    yorigin = min(t[4] for t in cells)
    try:
        spec = GridSpec(crs_authid, xorigin, yorigin, delr, delc, nrow, ncol)
    except GridError as exc:
        raise QgsProcessingException(f"Grid layer: {exc}") from exc
    tol = 1e-6 * max(delr, delc)
    seen: Dict[Tuple[int, int], int] = {}
    for r, c, act, x0, y0, x1, y1, area in cells:
        if (r, c) in seen:
            raise QgsProcessingException(f"Grid layer: cell row={r}, col={c} appears more than once")
        if not (1 <= r <= nrow and 1 <= c <= ncol):
            raise QgsProcessingException(f"Grid layer: invalid index row={r}, col={c}")
        ex = spec.cell_bounds(r, c)
        if max(abs(x0 - ex[0]), abs(y0 - ex[1]), abs(x1 - ex[2]), abs(y1 - ex[3])) > tol or abs(area - delr * delc) > 1e-6 * delr * delc:
            raise QgsProcessingException(
                f"Grid layer: cell row={r}, col={c} is not where a uniform, unrotated grid with row 1 in the north "
                f"and col 1 in the west puts it (expected x {ex[0]:.6g}..{ex[2]:.6g}, y {ex[1]:.6g}..{ex[3]:.6g}).")
        seen[(r, c)] = act
    if len(seen) != nrow * ncol:
        raise QgsProcessingException(
            f"Grid layer holds {len(seen)} cells but its row/col span is {nrow} x {ncol} = {nrow * ncol}; the grid must be complete.")
    return spec, seen


def parts_of(geometry) -> List[List[Tuple[float, float]]]:
    """Vertex lists (x, y) of every part of a line geometry, curves linearised."""
    if geometry.isNull() or geometry.isEmpty():
        return []
    geom = QgsGeometry(geometry)
    geom.convertToStraightSegment()   # in place; no-op for straight lines
    raw = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
    return [[(p.x(), p.y()) for p in part] for part in raw if len(part) >= 2]
