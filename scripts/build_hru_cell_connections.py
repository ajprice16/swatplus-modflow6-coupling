#!/usr/bin/env python3
"""
Build the HRU-to-MODFLOW-cell spatial intersection table for the Verde River
watershed.

For each SWAT+ HRU polygon that overlaps a MODFLOW active grid cell, the table
records the overlap area and the two fractions needed for coupling:

  frac_of_hru  — what fraction of this HRU lies in this cell
                 (used to allocate cell-level recharge *from* SWAT+ outputs)
  frac_of_cell — what fraction of the cell area is covered by this HRU
                 (used to weight contributions when multiple HRUs share a cell)

The output CSV is the spatial bridge that makes dynamic coupling possible:
  SWAT+ HRU percolation [mm/d] × frac_of_hru × hru_area_m2 → m³/d per cell

Reference: Kim et al. (2008) Fig. 2–3; Bailey et al. (2020) swatmf_grid2dhru.txt

Inputs:
    models/modflow/verde_river/                 (built by build_modflow_model.py)
    QGISProjects/VerdeRiver/Watershed/Shapes/hrus2.shp

Outputs:
    models/connections/verde_river/hru_cell_connections.csv
    models/connections/verde_river/hru_cell_connections.gpkg

Usage:
    python scripts/build_hru_cell_connections.py [--min-frac 0.001]
"""

from __future__ import annotations

import argparse
import logging
import sys
import warnings
from pathlib import Path

import numpy as np
import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import box
from shapely import STRtree
import flopy.mf6 as mf6
import flopy

warnings.filterwarnings("ignore", category=FutureWarning)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

REPO       = Path(__file__).resolve().parents[1]
MODEL_DIR  = REPO / "models" / "modflow" / "verde_river"
HRU_SHP    = Path("/home/exouser/Documents/QGISProjects/VerdeRiver/Watershed/Shapes/hrus2.shp")
OUT_DIR    = REPO / "models" / "connections" / "verde_river"
MODEL_CRS  = "EPSG:6404"


# ─────────────────────────────────────────────────────────────────────────────
# 1.  MODFLOW grid → active cell polygon GeoDataFrame
# ─────────────────────────────────────────────────────────────────────────────

def load_active_cell_polygons() -> gpd.GeoDataFrame:
    """
    Load the MODFLOW 6 model and return a GeoDataFrame with one 1 km² square
    polygon per active layer-1 cell, indexed by (mf_row, mf_col).
    """
    log.info("Loading MODFLOW 6 model from %s …", MODEL_DIR)
    sim = flopy.mf6.MFSimulation.load(
        sim_name="verde_river",
        sim_ws=str(MODEL_DIR),
        verbosity_level=0,
    )
    gwf    = sim.get_model()
    dis    = gwf.dis
    nrow   = int(dis.nrow.get_data())
    ncol   = int(dis.ncol.get_data())
    delr   = dis.delr.get_data()          # array of column widths (W→E)
    delc   = dis.delc.get_data()          # array of row heights (N→S)
    xorig  = float(dis.xorigin.get_data())
    yorig  = float(dis.yorigin.get_data())
    idom   = dis.idomain.get_data()[0]    # layer-1 idomain (nrow × ncol)

    log.info("  Grid: %d rows × %d cols  origin=(%.0f, %.0f)",
             nrow, ncol, xorig, yorig)

    # Build column and row edge arrays
    col_edges = np.concatenate([[xorig], xorig + np.cumsum(delr)])
    # Row 0 is at the top (north); yorigin is the south edge of the domain
    ymax      = yorig + np.sum(delc)
    row_edges = np.concatenate([[ymax], ymax - np.cumsum(delc)])

    records = []
    for r in range(nrow):
        for c in range(ncol):
            if idom[r, c] != 1:
                continue
            x0, x1 = col_edges[c],     col_edges[c + 1]
            y0, y1 = row_edges[r + 1], row_edges[r]      # y0 < y1
            records.append({
                "mf_row":       r,
                "mf_col":       c,
                "cell_area_m2": float((x1 - x0) * (y1 - y0)),
                "geometry":     box(x0, y0, x1, y1),
            })

    gdf = gpd.GeoDataFrame(records, crs=MODEL_CRS)
    log.info("  Active cells loaded: %d", len(gdf))
    return gdf


# ─────────────────────────────────────────────────────────────────────────────
# 2.  SWAT+ HRU polygons
# ─────────────────────────────────────────────────────────────────────────────

def load_hru_polygons() -> gpd.GeoDataFrame:
    """
    Load hrus2.shp and return a clean GeoDataFrame with:
      hru_id      — global SWAT+ HRU id (int, matches id/gis_id in hru.con)
      subbasin    — SWAT+ subbasin number
      hru_area_m2 — polygon area in m²  (computed from geometry, not Area column)
    """
    log.info("Loading HRU polygons from %s …", HRU_SHP)
    hrus = gpd.read_file(HRU_SHP)

    if str(hrus.crs) != MODEL_CRS:
        log.info("  Reprojecting HRUs from %s → %s", hrus.crs, MODEL_CRS)
        hrus = hrus.to_crs(MODEL_CRS)

    hrus["hru_id"]      = hrus["HRUS"].astype(int)
    hrus["subbasin"]    = hrus["Subbasin"].astype(int)
    hrus["hru_area_m2"] = hrus.geometry.area

    log.info("  HRUs loaded: %d  (subbasins: %d)",
             len(hrus), hrus["subbasin"].nunique())

    # Drop slivers with essentially zero area
    before = len(hrus)
    hrus = hrus[hrus["hru_area_m2"] > 1.0]
    if len(hrus) < before:
        log.info("  Dropped %d near-zero-area polygons", before - len(hrus))

    return hrus[["hru_id", "subbasin", "hru_area_m2", "geometry"]]


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Spatial intersection
# ─────────────────────────────────────────────────────────────────────────────

def intersect(hrus: gpd.GeoDataFrame,
              cells: gpd.GeoDataFrame,
              min_frac: float) -> gpd.GeoDataFrame:
    """
    Intersect HRU polygons with active cell polygons using Shapely 2 STRtree.

    Strategy:
      1. STRtree.query(all HRUs at once, predicate="intersects") → candidate pairs
      2. shapely.intersection() vectorised over only those pairs (no Python loop)
      3. Filter by area threshold

    For 8k HRUs × 17k cells this runs in ~5–30 s vs hours for gpd.overlay.
    """
    log.info("Computing spatial intersection (Shapely 2 STRtree) …")

    hru_geoms  = hrus.geometry.values
    cell_geoms = cells.geometry.values

    # Bulk spatial query — returns arrays of matching indices
    tree = STRtree(cell_geoms)
    hru_idx, cell_idx = tree.query(hru_geoms, predicate="intersects")
    log.info("  Candidate pairs from STRtree: %d", len(hru_idx))

    # Vectorised intersection and area
    inter_geoms = shapely.intersection(hru_geoms[hru_idx], cell_geoms[cell_idx])
    inter_areas = shapely.area(inter_geoms)

    hru_areas  = hrus["hru_area_m2"].values[hru_idx]
    cell_areas = cells["cell_area_m2"].values[cell_idx]
    frac_hru   = inter_areas / np.where(hru_areas  > 0, hru_areas,  1)
    frac_cell  = inter_areas / np.where(cell_areas > 0, cell_areas, 1)
    frac_min   = np.minimum(frac_hru, frac_cell)

    mask = (inter_areas > 0) & (frac_min >= min_frac)
    log.info("  Pairs after filter (min_frac=%.4f): %d", min_frac, int(mask.sum()))

    result = gpd.GeoDataFrame({
        "hru_id":          hrus["hru_id"].values[hru_idx[mask]],
        "subbasin":        hrus["subbasin"].values[hru_idx[mask]],
        "mf_row":          cells["mf_row"].values[cell_idx[mask]],
        "mf_col":          cells["mf_col"].values[cell_idx[mask]],
        "hru_area_m2":     hru_areas[mask],
        "cell_area_m2":    cell_areas[mask],
        "overlap_area_m2": inter_areas[mask],
        "frac_of_hru":     frac_hru[mask],
        "frac_of_cell":    frac_cell[mask],
        "geometry":        inter_geoms[mask],
    }, crs=MODEL_CRS)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Summary statistics
# ─────────────────────────────────────────────────────────────────────────────

def report(ov: gpd.GeoDataFrame) -> None:
    hrus_covered  = ov["hru_id"].nunique()
    cells_covered = ov.groupby(["mf_row", "mf_col"]).ngroups
    avg_hrus_cell = ov.groupby(["mf_row", "mf_col"])["hru_id"].count().mean()
    avg_cells_hru = ov.groupby("hru_id")[["mf_row"]].count().mean().iloc[0]

    log.info("Intersection summary:")
    log.info("  HRUs with ≥1 cell match : %d", hrus_covered)
    log.info("  Cells with ≥1 HRU match : %d", cells_covered)
    log.info("  Avg HRUs per cell        : %.2f", avg_hrus_cell)
    log.info("  Avg cells per HRU        : %.2f", avg_cells_hru)

    # Check how well area is conserved (intersection vs original)
    total_hru_area_km2  = ov.groupby("hru_id")["hru_area_m2"].first().sum() / 1e6
    total_ovlp_area_km2 = ov["overlap_area_m2"].sum() / 1e6
    log.info("  Total HRU area (unique)  : %.1f km²", total_hru_area_km2)
    log.info("  Total overlap area       : %.1f km²", total_ovlp_area_km2)
    log.info("  Area conservation        : %.2f %%",
             100 * total_ovlp_area_km2 / total_hru_area_km2)


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Output
# ─────────────────────────────────────────────────────────────────────────────

def save(ov: gpd.GeoDataFrame, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    cols_csv = [
        "hru_id", "subbasin",
        "mf_row", "mf_col",
        "hru_area_m2", "cell_area_m2",
        "overlap_area_m2", "frac_of_hru", "frac_of_cell",
    ]
    out_csv = out_dir / "hru_cell_connections.csv"
    (ov[cols_csv]
        .sort_values(["hru_id", "mf_row", "mf_col"])
        .to_csv(out_csv, index=False, float_format="%.6f"))
    log.info("Saved CSV  → %s  (%d rows)", out_csv, len(ov))

    out_gpkg = out_dir / "hru_cell_connections.gpkg"
    ov[cols_csv + ["geometry"]].to_file(
        out_gpkg, layer="hru_cell_intersections", driver="GPKG")
    log.info("Saved GPKG → %s", out_gpkg)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--min-frac", type=float, default=0.001,
        help="Drop intersection fragments smaller than this fraction of the "
             "smaller polygon (default 0.001 = 0.1%%).")
    args = ap.parse_args()

    cells = load_active_cell_polygons()
    hrus  = load_hru_polygons()
    ov    = intersect(hrus, cells, min_frac=args.min_frac)
    report(ov)
    save(ov, OUT_DIR)
    log.info("Done.")


if __name__ == "__main__":
    main()
