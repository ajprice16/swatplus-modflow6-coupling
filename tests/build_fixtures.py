"""Synthetic GIS layers for the builder tests (invented geometry; no real watershed)."""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import yaml
from rasterio.transform import from_origin
from shapely.geometry import LineString, box

CRS = "EPSG:3857"                       # any projected CRS; this one is only a test label
NROW, NCOL, CELL = 10, 12, 50.0
X0, Y0 = 1000.0, 2000.0                  # lower-left corner (arbitrary synthetic coordinates)


def grid_json(path: Path) -> dict:
    d = {"format": "swatplus-modflow-grid/1", "crs": CRS, "xorigin": X0, "yorigin": Y0, "angrot": 0.0,
         "delr": CELL, "delc": CELL, "nrow": NROW, "ncol": NCOL}
    path.write_text(json.dumps(d))
    return d


def write_raster(path: Path, arr: np.ndarray, pix: float = 25.0, nodata: float = -9999.0) -> None:
    """Raster covering the grid with 2x finer pixels than the model cells."""
    h, w = NROW * 2, NCOL * 2
    data = np.asarray(arr, dtype="float32")
    assert data.shape == (h, w)
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1, dtype="float32", crs=CRS,
                       transform=from_origin(X0, Y0 + NROW * CELL, pix, pix), nodata=nodata) as dst:
        dst.write(data, 1)


def dem_array() -> np.ndarray:
    h, w = NROW * 2, NCOL * 2
    yy, xx = np.mgrid[0:h, 0:w]
    return 100.0 - 0.1 * yy - 0.05 * xx                     # gently sloping surface, metres


def cell_box(r0, r1, c0, c1):
    """Polygon covering cells rows r0..r1, cols c0..c1 (0-based, inclusive)."""
    xmin, xmax = X0 + c0 * CELL, X0 + (c1 + 1) * CELL
    ymax = Y0 + NROW * CELL - r0 * CELL
    ymin = Y0 + NROW * CELL - (r1 + 1) * CELL
    return box(xmin, ymin, xmax, ymax)


def write_layers(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    grid_json(root / "grid.json")
    write_raster(root / "dem.tif", dem_array())
    gpd.GeoDataFrame({"id": [1]}, geometry=[cell_box(0, NROW - 1, 0, NCOL - 2)], crs=CRS).to_file(
        root / "domain.gpkg", driver="GPKG")
    # zones: two local zones inside a regional one that covers every active cell
    gpd.GeoDataFrame({"name": ["a", "b", "regional"], "k": [0, 0, 0]},
                     geometry=[cell_box(1, 3, 1, 3), cell_box(5, 7, 5, 8), cell_box(0, NROW - 1, 0, NCOL - 1)],
                     crs=CRS).to_file(root / "zones.gpkg", driver="GPKG")
    # channels: ch 1 runs west->east along row 3 then joins ch 2 which runs south along column 8
    ch1 = LineString([(X0 + 10, Y0 + (NROW - 3.5) * CELL), (X0 + 8.5 * CELL, Y0 + (NROW - 3.5) * CELL)])
    ch2 = LineString([(X0 + 8.5 * CELL, Y0 + (NROW - 3.5) * CELL), (X0 + 8.5 * CELL, Y0 + 0.5 * CELL)])
    gpd.GeoDataFrame({"Channel": [1, 2]}, geometry=[ch1, ch2], crs=CRS).to_file(root / "channels.gpkg", driver="GPKG")
    # HRUs: three polygons, the last one hangs over the east edge of the grid
    gpd.GeoDataFrame({"HRUS": [1, 2, 3]}, geometry=[
        cell_box(0, 4, 0, 5), cell_box(5, 9, 0, 5), box(X0 + 6 * CELL, Y0, X0 + (NCOL + 2) * CELL, Y0 + NROW * CELL)],
        crs=CRS).to_file(root / "hrus.gpkg", driver="GPKG")
    (root / "topology.csv").write_text("channel_id,downstream_id\n1,2\n2,0\n")


def spec_dict(zone_priorities=(10, 20, 1), **over) -> dict:
    s = {
        "build_version": 1, "name": "synthetic-build", "grid": "grid.json",
        "output": {"directory": "model", "model_name": "gwf"},
        "domain": {"polygons": {"path": "domain.gpkg"}},
        "top": {"dem": {"path": "dem.tif"}},
        "layers": [{"name": "upper", "thickness_m": 20.0}, {"name": "lower", "thickness_m": 40.0}],
        "min_thickness_m": 1.0,
        "properties": {
            "default": {"k_m_per_day": 1.0, "k33_over_k": 0.1, "sy": 0.1, "ss_per_m": 1.0e-5},
            "zones": [
                {"name": "a", "polygons": {"path": "zones.gpkg"}, "where": {"field": "name", "equals": "a"},
                 "priority": zone_priorities[0], "k_m_per_day": 10.0},
                {"name": "b", "polygons": {"path": "zones.gpkg"}, "where": {"field": "name", "equals": "b"},
                 "priority": zone_priorities[1], "k_m_per_day": 0.05},
                {"name": "regional", "polygons": {"path": "zones.gpkg"}, "where": {"field": "name", "equals": "regional"},
                 "priority": zone_priorities[2], "k_m_per_day": 2.0},
            ],
        },
        "initial_heads": {"mode": "below_top", "depth_m": 5.0},
    }
    s.update(over)
    return s


def write_spec(root: Path, spec: dict, name="build.yaml") -> Path:
    p = root / name
    p.write_text(yaml.safe_dump(spec, sort_keys=False))
    return p
