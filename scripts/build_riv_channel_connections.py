#!/usr/bin/env python3
"""
Build the MODFLOW RIV cell → SWAT+ channel connection file for the Verde River.

Each MODFLOW RIV cell is snapped to the nearest SWAT+ channel reach.  The
resulting table is used in the daily coupling loop to:

  1. Set MODFLOW RIV stage from SWAT+ channel water surface elevation
  2. Return MODFLOW GW/SW exchange fluxes to the correct SWAT+ channel

Connection logic (Bailey et al. 2020, modflow.con equivalent):
  - Each RIV cell belongs to exactly one SWAT+ channel (nearest-line snap)
  - Stage assignment: stage[cell] = channel_min_elev + swat_depth[channel]
  - Flux return: net RIV flux per cell is summed by channel and fed to SWAT+

Inputs:
    models/modflow/verde_river/           (MODFLOW 6 model with RIV package)
    QGISProjects/VerdeRiver/Watershed/Shapes/rivs1.shp  (SWAT+ channel reaches)

Outputs:
    models/connections/verde_river/riv_channel_connections.csv
    models/connections/verde_river/riv_channel_connections.gpkg

Usage:
    python scripts/build_riv_channel_connections.py [--max-snap 5000]
"""

from __future__ import annotations

import argparse
import logging
import warnings
from pathlib import Path

import numpy as np
import geopandas as gpd
import pandas as pd
import shapely
from shapely import STRtree
from shapely.geometry import Point
import flopy

warnings.filterwarnings("ignore", category=FutureWarning)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

REPO      = Path(__file__).resolve().parents[1]
MODEL_DIR = REPO / "models" / "modflow" / "verde_river"
RIVS_SHP  = Path("/home/exouser/Documents/QGISProjects/VerdeRiver/Watershed/Shapes/rivs1.shp")
OUT_DIR   = REPO / "models" / "connections" / "verde_river"
MODEL_CRS = "EPSG:6404"


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Extract RIV cells from built MODFLOW model
# ─────────────────────────────────────────────────────────────────────────────

def load_riv_cells() -> gpd.GeoDataFrame:
    """
    Load the MODFLOW model and return a GeoDataFrame with one point per RIV
    cell, located at the cell centre.  Includes stage, cond, and rbot from the
    RIV stress period data.
    """
    log.info("Loading MODFLOW 6 model …")
    sim = flopy.mf6.MFSimulation.load(
        sim_name="verde_river",
        sim_ws=str(MODEL_DIR),
        verbosity_level=0,
    )
    gwf = sim.get_model()
    dis = gwf.dis

    nrow   = int(dis.nrow.get_data())
    ncol   = int(dis.ncol.get_data())
    delr   = dis.delr.get_data()
    delc   = dis.delc.get_data()
    xorig  = float(dis.xorigin.get_data())
    yorig  = float(dis.yorigin.get_data())

    # Column and row centre coordinates
    col_centres = xorig + np.cumsum(delr) - delr / 2.0
    ymax        = yorig + np.sum(delc)
    row_centres = ymax - (np.cumsum(delc) - delc / 2.0)

    # Read RIV stress period data
    riv_data = gwf.riv.stress_period_data.get_data()[0]
    log.info("  RIV cells in model: %d", len(riv_data))

    records = []
    for rec in riv_data:
        lay, row, col = rec["cellid"]
        x = float(col_centres[col])
        y = float(row_centres[row])
        records.append({
            "mf_row":    row,
            "mf_col":    col,
            "riv_stage": float(rec["stage"]),
            "riv_cond":  float(rec["cond"]),
            "riv_rbot":  float(rec["rbot"]),
            "geometry":  Point(x, y),
        })

    gdf = gpd.GeoDataFrame(records, crs=MODEL_CRS)
    log.info("  Loaded %d RIV cell records", len(gdf))
    return gdf


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Load SWAT+ channel reaches
# ─────────────────────────────────────────────────────────────────────────────

def load_channels() -> gpd.GeoDataFrame:
    """Load rivs1.shp and return relevant channel attributes."""
    log.info("Loading SWAT+ channel reaches from %s …", RIVS_SHP)
    rivs = gpd.read_file(RIVS_SHP)

    if str(rivs.crs) != MODEL_CRS:
        log.info("  Reprojecting from %s → %s", rivs.crs, MODEL_CRS)
        rivs = rivs.to_crs(MODEL_CRS)

    rivs = rivs.rename(columns={
        "Channel":   "channel_id",
        "Subbasin":  "subbasin",
        "Len2":      "length_m",
        "Wid2":      "width_m",
        "Dep2":      "depth_m",
        "MinEl":     "min_elev_m",
        "MaxEl":     "max_elev_m",
        "strmOrder": "stream_order",
    })

    # Convert elevations from feet if needed (same check as build_modflow_model.py)
    if rivs["min_elev_m"].median() > 5000:
        log.info("  Converting elevations feet → metres")
        rivs["min_elev_m"] *= 0.3048
        rivs["max_elev_m"] *= 0.3048
        rivs["width_m"]    *= 0.3048
        rivs["depth_m"]    *= 0.3048
        rivs["length_m"]   *= 0.3048

    keep = ["channel_id", "subbasin", "length_m", "width_m", "depth_m",
            "min_elev_m", "max_elev_m", "stream_order", "geometry"]
    log.info("  Channels loaded: %d  (reaches: %d unique)",
             len(rivs), rivs["channel_id"].nunique())
    return rivs[keep]


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Snap RIV cells to nearest channel reach
# ─────────────────────────────────────────────────────────────────────────────

def snap_to_channels(riv_cells: gpd.GeoDataFrame,
                     channels: gpd.GeoDataFrame,
                     max_snap_m: float) -> gpd.GeoDataFrame:
    """
    For each RIV cell centre, find the nearest SWAT+ channel line and record
    the channel id and snap distance.  Cells further than max_snap_m from any
    channel are flagged as unmatched.
    """
    log.info("Snapping %d RIV cells to %d channel reaches …",
             len(riv_cells), len(channels))

    cell_pts   = riv_cells.geometry.values          # array of Point
    chan_lines = channels.geometry.values           # array of LineString

    # STRtree.nearest returns index of nearest tree geometry per input geometry
    tree = STRtree(chan_lines)
    nearest_idx = tree.nearest(cell_pts)            # shape: (n_cells,)

    # Compute snap distances (point → nearest point on line)
    nearest_lines   = chan_lines[nearest_idx]
    snap_pts        = shapely.snap(cell_pts, nearest_lines, tolerance=0)
    nearest_on_line = shapely.ops.nearest_points(
        shapely.from_wkt(shapely.to_wkt(cell_pts)),
        shapely.from_wkt(shapely.to_wkt(nearest_lines)),
    )[1]
    snap_dist = shapely.distance(cell_pts, nearest_lines)

    # Join channel attributes
    chan_attrs = channels.iloc[nearest_idx][
        ["channel_id", "subbasin", "min_elev_m", "max_elev_m",
         "width_m", "depth_m", "length_m", "stream_order"]
    ].reset_index(drop=True)

    result = riv_cells.copy().reset_index(drop=True)
    result["channel_id"]    = chan_attrs["channel_id"].values
    result["chan_subbasin"]  = chan_attrs["subbasin"].values
    result["chan_min_elev"]  = chan_attrs["min_elev_m"].values
    result["chan_max_elev"]  = chan_attrs["max_elev_m"].values
    result["chan_width_m"]   = chan_attrs["width_m"].values
    result["chan_depth_m"]   = chan_attrs["depth_m"].values
    result["chan_length_m"]  = chan_attrs["length_m"].values
    result["stream_order"]   = chan_attrs["stream_order"].values
    result["snap_dist_m"]    = snap_dist

    unmatched = (snap_dist > max_snap_m).sum()
    if unmatched > 0:
        log.warning("  %d RIV cells > %.0f m from any channel — check geometry",
                    unmatched, max_snap_m)
    result["matched"] = snap_dist <= max_snap_m

    log.info("  Matched: %d / %d  (max snap: %.0f m)",
             int(result["matched"].sum()), len(result), max_snap_m)
    log.info("  Snap distance — median: %.0f m  max: %.0f m",
             float(np.median(snap_dist)), float(snap_dist.max()))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Summary and save
# ─────────────────────────────────────────────────────────────────────────────

def report(conn: gpd.GeoDataFrame) -> None:
    matched = conn[conn["matched"]]
    cells_per_chan = matched.groupby("channel_id").size()
    log.info("Connection summary:")
    log.info("  Channels with ≥1 RIV cell : %d / %d",
             cells_per_chan.shape[0],
             conn["channel_id"].nunique())
    log.info("  Avg RIV cells per channel  : %.1f", cells_per_chan.mean())
    log.info("  Max RIV cells per channel  : %d",   cells_per_chan.max())
    log.info("  Stream order distribution  :")
    for order, cnt in matched.groupby("stream_order").size().items():
        log.info("    order %d: %d cells", order, cnt)


def save(conn: gpd.GeoDataFrame, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    cols_csv = [
        "mf_row", "mf_col",
        "channel_id", "chan_subbasin",
        "riv_stage", "riv_cond", "riv_rbot",
        "chan_min_elev", "chan_max_elev", "chan_width_m", "chan_depth_m",
        "chan_length_m", "stream_order", "snap_dist_m", "matched",
    ]
    out_csv = out_dir / "riv_channel_connections.csv"
    (conn[cols_csv]
        .sort_values(["channel_id", "mf_row", "mf_col"])
        .to_csv(out_csv, index=False, float_format="%.4f"))
    log.info("Saved CSV  → %s  (%d rows)", out_csv, len(conn))

    out_gpkg = out_dir / "riv_channel_connections.gpkg"
    conn[cols_csv + ["geometry"]].to_file(
        out_gpkg, layer="riv_channel_connections", driver="GPKG")
    log.info("Saved GPKG → %s", out_gpkg)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--max-snap", type=float, default=5000.0,
        help="Flag RIV cells further than this distance (m) from any channel "
             "(default 5000 m = 5 km, generous for 1km grid).")
    args = ap.parse_args()

    riv_cells = load_riv_cells()
    channels  = load_channels()
    conn      = snap_to_channels(riv_cells, channels, max_snap_m=args.max_snap)
    report(conn)
    save(conn, OUT_DIR)
    log.info("Done.")


if __name__ == "__main__":
    main()
