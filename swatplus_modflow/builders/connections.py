"""Write ``hru_cells.csv`` and ``channel_cells.csv`` (docs/DATA_CONTRACTS.md sections 3 and 4).

Inputs are QSWAT+ HRU polygons, channel lines and the model grid (``grid.json``,
optionally with a grid polygon layer whose ``active`` field limits the cells).
Field names are parameters whose defaults are the QSWAT+ names (HRU id ``HRUS``,
channel id ``Channel``).

Rules enforced here (``.cursorrules`` rules 3 and 4): every layer must carry a
CRS equal to the grid CRS (or be reprojected on request, never assumed), the CRS
must be projected because areas and lengths are measured, and indices written
are 1-based.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..utils.coupling_invariants import CouplingInvariantError, check_projected_crs
from .grid import BuildInputError, Grid, crs_equal, read_grid

log = logging.getLogger(__name__)
HRU_FORMAT = "swatplus-modflow-hru-cells/1"
CHANNEL_FORMAT = "swatplus-modflow-channel-cells/1"


def _prepare(gdf, grid: Grid, name: str, reproject: bool):
    if gdf.crs is None:
        raise BuildInputError(f"{name}: layer has no CRS; refusing to assume one")
    if not crs_equal(gdf.crs, grid.crs):
        if not reproject:
            raise BuildInputError(f"{name}: CRS {gdf.crs} differs from the grid CRS {grid.crs}. "
                                  "Reproject the layer, or pass --reproject to do it here")
        gdf = gdf.to_crs(grid.crs)
    try:
        check_projected_crs(grid.crs, name)
    except CouplingInvariantError as exc:
        raise BuildInputError(str(exc)) from None
    return gdf


def read_active_cells(grid_layer: Path, layer: Optional[str], grid: Grid) -> np.ndarray:
    """Boolean ``(nrow, ncol)`` from a grid polygon layer's integer ``row``, ``col``, ``active`` fields."""
    import geopandas as gpd
    g = gpd.read_file(grid_layer, layer=layer) if layer else gpd.read_file(grid_layer)
    for f in ("row", "col", "active"):
        if f not in g.columns:
            raise BuildInputError(f"{Path(grid_layer).name}: grid layer needs a '{f}' field")
    r, c = g["row"].to_numpy(int) - 1, g["col"].to_numpy(int) - 1
    if (r < 0).any() or (c < 0).any() or (r >= grid.nrow).any() or (c >= grid.ncol).any():
        raise BuildInputError(f"{Path(grid_layer).name}: row/col outside grid.json (indices are 1-based)")
    act = np.zeros((grid.nrow, grid.ncol), dtype=bool)
    act[r, c] = g["active"].to_numpy(int) == 1
    return act


def _candidate_cells(bounds, grid: Grid):
    """Cell boxes covering ``bounds`` as arrays (rows, cols, boxes)."""
    import shapely
    xmin, ymin, xmax, ymax = bounds
    c0 = max(int(np.floor((xmin - grid.xorigin) / grid.delr)), 0)
    c1 = min(int(np.floor((xmax - grid.xorigin) / grid.delr)), grid.ncol - 1)
    r0 = max(int(np.floor((grid.ytop - ymax) / grid.delc)), 0)
    r1 = min(int(np.floor((grid.ytop - ymin) / grid.delc)), grid.nrow - 1)
    if c1 < c0 or r1 < r0:
        return np.array([], int), np.array([], int), np.array([], object)
    rr, cc = np.meshgrid(np.arange(r0, r1 + 1), np.arange(c0, c1 + 1), indexing="ij")
    rr, cc = rr.ravel(), cc.ravel()
    x0 = grid.xorigin + cc * grid.delr
    y1 = grid.ytop - rr * grid.delc
    return rr, cc, shapely.box(x0, y1 - grid.delc, x0 + grid.delr, y1)


def build_hru_cells(hrus, grid: Grid, *, id_field: str = "HRUS", layer: int = 1,
                    active: Optional[np.ndarray] = None, min_fraction: float = 0.0,
                    reproject: bool = False) -> Tuple[pd.DataFrame, dict]:
    """HRU polygon x grid cell intersections.

    ``fraction`` = area of the HRU inside the cell / total polygon area of the HRU (including any
    part outside the grid), so an HRU partly outside the grid has fractions summing to less than 1.
    """
    import shapely
    from shapely import STRtree
    hrus = _prepare(hrus, grid, "HRU layer", reproject)
    if id_field not in hrus.columns:
        raise BuildInputError(f"HRU layer has no field '{id_field}' (fields: {list(hrus.columns)})")
    try:
        ids = hrus[id_field].astype(float)
        if (ids != np.floor(ids)).any() or ids.isna().any():
            raise ValueError
        ids = ids.astype(np.int64)
    except (ValueError, TypeError):
        raise BuildInputError(f"HRU field '{id_field}' must hold integer ids") from None
    geoms = hrus.geometry.values
    bad = shapely.is_empty(geoms) | shapely.is_missing(geoms)
    if bad.any():
        raise BuildInputError(f"HRU layer has {int(bad.sum())} empty geometries")
    area_poly = shapely.area(geoms)
    total_area = pd.Series(area_poly).groupby(ids.to_numpy()).sum()
    rr, cc, boxes = _candidate_cells(tuple(shapely.total_bounds(geoms)), grid)
    rows = []
    if len(rr):
        tree = STRtree(boxes)
        hi, ci = tree.query(geoms, predicate="intersects")
        inter = shapely.area(shapely.intersection(geoms[hi], boxes[ci]))
        df = pd.DataFrame({"hru_id": ids.to_numpy()[hi], "row0": rr[ci], "col0": cc[ci], "area_m2": inter})
        df = df[df["area_m2"] > 0].groupby(["hru_id", "row0", "col0"], as_index=False)["area_m2"].sum()
        df["fraction"] = df["area_m2"] / df["hru_id"].map(total_area)
        if active is not None:
            df = df[active[df["row0"].to_numpy(), df["col0"].to_numpy()]]
        n_small = int((df["fraction"] < min_fraction).sum())
        df = df[df["fraction"] >= min_fraction]
        df = df.assign(layer=layer, row=df["row0"] + 1, col=df["col0"] + 1)
        rows = df.sort_values(["hru_id", "row", "col"])[["hru_id", "layer", "row", "col", "area_m2", "fraction"]]
    out = rows if len(rows) else pd.DataFrame(columns=["hru_id", "layer", "row", "col", "area_m2", "fraction"])
    fsum = out.groupby("hru_id")["fraction"].sum() if len(out) else pd.Series(dtype=float)
    all_ids = sorted(set(int(i) for i in ids))
    outside = [i for i in all_ids if i not in set(fsum.index)]
    partial = fsum[fsum < 1 - 1e-6]
    report = dict(format=HRU_FORMAT, n_hrus=len(all_ids), n_rows=int(len(out)),
                  hrus_outside_grid=outside, n_hrus_partly_outside=int(len(partial)),
                  min_fraction_sum=float(fsum.min()) if len(fsum) else None,
                  area_outside_grid_m2=float(sum((1 - fsum.get(i, 0.0)) * total_area[i] for i in all_ids)))
    return out, report


def _line_parts(geom):
    from shapely.ops import linemerge
    if geom.geom_type == "LineString":
        return geom
    if geom.geom_type == "MultiLineString":
        m = linemerge(geom)
        if m.geom_type == "LineString":
            return m
        raise BuildInputError("a channel is a multi-part line that cannot be merged into one line")
    raise BuildInputError(f"channel geometries must be lines, got {geom.geom_type}")


def build_channel_cells(channels, grid: Grid, *, id_field: str = "Channel", layer: int = 1,
                        active: Optional[np.ndarray] = None, direction: str = "as_drawn",
                        dem: Optional[np.ndarray] = None, reproject: bool = False) -> Tuple[pd.DataFrame, dict]:
    """Channel line x grid cell pieces ordered from the upstream end.

    ``direction``: ``as_drawn`` (vertices run upstream -> downstream), ``reversed``, or ``dem``
    (the end with the lower mean ground elevation, from the ``(nrow, ncol)`` array ``dem``, is
    downstream). ``seq`` follows that order; a channel entering the same cell twice yields one
    row for that cell, placed at the mean position of its pieces.
    """
    import shapely
    from shapely import STRtree
    if direction not in ("as_drawn", "reversed", "dem"):
        raise BuildInputError("direction must be as_drawn, reversed or dem")
    if direction == "dem" and dem is None:
        raise BuildInputError("direction 'dem' needs a DEM sampled on the grid")
    channels = _prepare(channels, grid, "channel layer", reproject)
    if id_field not in channels.columns:
        raise BuildInputError(f"channel layer has no field '{id_field}' (fields: {list(channels.columns)})")
    ids = channels[id_field].astype(int).to_numpy()
    if len(set(ids)) != len(ids):
        raise BuildInputError(f"channel field '{id_field}' has duplicate values; one line per channel is required")
    lines = [_line_parts(g) for g in channels.geometry.values]
    rr, cc, boxes = _candidate_cells(tuple(shapely.total_bounds(np.array(lines, dtype=object))), grid)
    recs: List[dict] = []
    no_cells, short = [], []
    if len(rr):
        tree = STRtree(boxes)
        for cid, line in zip(ids, lines):
            total = line.length
            ci = tree.query(line, predicate="intersects")
            if len(ci) == 0:
                no_cells.append(int(cid))
                continue
            pieces = shapely.intersection(line, boxes[ci])
            parts = []
            for p, k in zip(pieces, ci):
                if p.is_empty or p.length <= 0:
                    continue
                if active is not None and not active[rr[k], cc[k]]:
                    continue
                segs = [p] if p.geom_type == "LineString" else [g for g in getattr(p, "geoms", []) if g.length > 0]
                for s in segs:
                    mid = line.project(s.interpolate(0.5, normalized=True))
                    parts.append((int(rr[k]), int(cc[k]), s.length, mid))
            if not parts:
                no_cells.append(int(cid))
                continue
            df = pd.DataFrame(parts, columns=["row0", "col0", "length_m", "pos"])
            df["w"] = df["length_m"] * df["pos"]
            g = df.groupby(["row0", "col0"], as_index=False).agg(length_m=("length_m", "sum"), w=("w", "sum"))
            g["pos"] = g["w"] / g["length_m"]
            flip = direction == "reversed"
            if direction == "dem":
                p0, p1 = line.interpolate(0.0), line.interpolate(total)
                r0, c0, ok0 = grid.cell_of(np.array([p0.x]), np.array([p0.y]))
                r1, c1, ok1 = grid.cell_of(np.array([p1.x]), np.array([p1.y]))
                if not (ok0[0] and ok1[0]) or np.isnan(dem[r0[0], c0[0]]) or np.isnan(dem[r1[0], c1[0]]):
                    raise BuildInputError(f"channel {cid}: end points lie outside the DEM; cannot infer direction")
                flip = dem[r0[0], c0[0]] < dem[r1[0], c1[0]]
            g = g.sort_values("pos", ascending=not flip)
            for k, r in enumerate(g.itertuples(index=False), start=1):
                recs.append(dict(channel_id=int(cid), seq=k, layer=layer, row=r.row0 + 1, col=r.col0 + 1,
                                 length_m=float(r.length_m), fraction=float(r.length_m / total)))
            if g["length_m"].sum() / total < 0.5:
                short.append(int(cid))
    out = pd.DataFrame(recs, columns=["channel_id", "seq", "layer", "row", "col", "length_m", "fraction"])
    report = dict(format=CHANNEL_FORMAT, n_channels=len(ids), n_rows=int(len(out)), channels_without_cells=no_cells,
                  channels_mostly_outside=short, direction=direction)
    return out, report


def write_connections(out_dir: Path, hru_df: Optional[pd.DataFrame], hru_report: Optional[dict],
                      ch_df: Optional[pd.DataFrame], ch_report: Optional[dict]) -> Dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    if hru_df is not None:
        hru_df.to_csv(out_dir / "hru_cells.csv", index=False, float_format="%.6f")
        files["hru_cells"] = "hru_cells.csv"
    if ch_df is not None:
        ch_df.to_csv(out_dir / "channel_cells.csv", index=False, float_format="%.6f")
        files["channel_cells"] = "channel_cells.csv"
    (out_dir / "connections_report.json").write_text(json.dumps(
        {"hru_cells": hru_report, "channel_cells": ch_report, "files": files}, indent=2))
    return files
