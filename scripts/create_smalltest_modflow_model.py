#!/usr/bin/env python3
"""Create a small MODFLOW 6 model over a QSWAT+ watershed for coupling development.

This is a first-pass groundwater model for framework testing, not a calibrated
hydrogeologic model. It uses QSWAT+ LSU/watershed polygons for the active
domain, a regular structured grid, and simple perimeter constant heads so the
model can run immediately.

All watershed / executable paths are required CLI arguments — there are no
built-in study-area defaults.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path

import flopy
import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import box


# Defaults require explicit CLI paths — no case-study or machine path map.
DEFAULT_PROJECT = Path("path/to/qswatplus/project")
DEFAULT_SWAT_TXT = DEFAULT_PROJECT / "Scenarios/Default/TxtInOut"
DEFAULT_DOMAIN = DEFAULT_PROJECT / "Watershed/Shapes/lsus2.shp"
DEFAULT_OUTPUT = Path("models/smalltest/modflow")
DEFAULT_MF6 = Path("mf6")
MODEL_NAME = "smalltest_mf6"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a simple MODFLOW 6 model covering a QSWAT+ watershed."
    )
    parser.add_argument(
        "--domain",
        type=Path,
        required=True,
        help="Watershed/LSU polygon shapefile (required).",
    )
    parser.add_argument(
        "--swat-txt",
        type=Path,
        required=True,
        help="SWAT+ TxtInOut directory (required).",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--mf6", type=Path, default=DEFAULT_MF6)
    parser.add_argument("--cell-size", type=float, default=1000.0, help="Grid cell size in model CRS units")
    parser.add_argument("--padding", type=float, default=1000.0, help="Padding around watershed bounds")
    parser.add_argument("--aquifer-thickness", type=float, default=120.0)
    parser.add_argument("--initial-depth", type=float, default=35.0, help="Initial water table depth below land surface")
    parser.add_argument("--run", action="store_true", help="Run mf6 after writing the model")
    return parser.parse_args()


def read_hru_table(swat_txt: Path) -> pd.DataFrame:
    hru_con = swat_txt / "hru.con"
    if not hru_con.exists():
        raise FileNotFoundError(f"Missing SWAT+ HRU connection file: {hru_con}")

    df = pd.read_csv(hru_con, sep=r"\s+", skiprows=1, engine="python")
    df.columns = [str(col).strip() for col in df.columns]
    return df


def aligned_extent(bounds: np.ndarray, cell_size: float, padding: float) -> tuple[float, float, float, float]:
    xmin, ymin, xmax, ymax = bounds
    xmin = math.floor((xmin - padding) / cell_size) * cell_size
    ymin = math.floor((ymin - padding) / cell_size) * cell_size
    xmax = math.ceil((xmax + padding) / cell_size) * cell_size
    ymax = math.ceil((ymax + padding) / cell_size) * cell_size
    return xmin, ymin, xmax, ymax


def build_grid(
    domain: gpd.GeoDataFrame,
    cell_size: float,
    padding: float,
) -> tuple[gpd.GeoDataFrame, np.ndarray, tuple[float, float, float, float]]:
    xmin, ymin, xmax, ymax = aligned_extent(domain.total_bounds, cell_size, padding)
    ncol = int(round((xmax - xmin) / cell_size))
    nrow = int(round((ymax - ymin) / cell_size))
    watershed = domain.geometry.union_all()

    records = []
    idomain = np.zeros((nrow, ncol), dtype=np.int32)

    for row in range(1, nrow + 1):
        y_top = ymax - (row - 1) * cell_size
        y_bottom = y_top - cell_size
        for col in range(1, ncol + 1):
            x_left = xmin + (col - 1) * cell_size
            x_right = x_left + cell_size
            geom = box(x_left, y_bottom, x_right, y_top)
            active = geom.intersects(watershed)
            if active:
                idomain[row - 1, col - 1] = 1
            records.append(
                {
                    "cell_id": (row - 1) * ncol + col,
                    "mf_layer": 1,
                    "mf_row": row,
                    "mf_col": col,
                    "active": int(active),
                    "geometry": geom,
                }
            )

    grid = gpd.GeoDataFrame(records, crs=domain.crs)
    return grid, idomain, (xmin, ymin, xmax, ymax)


def fit_surface_from_hrus(hru_df: pd.DataFrame, target_crs: str) -> tuple[np.ndarray, np.ndarray]:
    transformer = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
    xs, ys = transformer.transform(hru_df["lon"].to_numpy(), hru_df["lat"].to_numpy())
    elev = hru_df["elev"].to_numpy(dtype=float)

    design = np.column_stack([np.ones(len(xs)), xs, ys])
    coeffs, *_ = np.linalg.lstsq(design, elev, rcond=None)
    return coeffs, np.array([float(np.nanmin(elev)), float(np.nanmax(elev))])


def surface_arrays(
    coeffs: np.ndarray,
    elev_range: np.ndarray,
    extent: tuple[float, float, float, float],
    nrow: int,
    ncol: int,
    cell_size: float,
    aquifer_thickness: float,
    initial_depth: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xmin, _ymin, _xmax, ymax = extent
    top = np.zeros((nrow, ncol), dtype=float)

    for i in range(nrow):
        y = ymax - (i + 0.5) * cell_size
        for j in range(ncol):
            x = xmin + (j + 0.5) * cell_size
            top[i, j] = coeffs[0] + coeffs[1] * x + coeffs[2] * y

    top = np.clip(top, elev_range[0] - 30.0, elev_range[1] + 30.0)
    botm = top - aquifer_thickness
    strt = top - initial_depth
    return top, botm, strt


def perimeter_constant_heads(idomain: np.ndarray, strt: np.ndarray) -> list[tuple[tuple[int, int, int], float]]:
    nrow, ncol = idomain.shape
    records = []
    for i in range(nrow):
        for j in range(ncol):
            if idomain[i, j] == 0:
                continue
            neighbors = [(i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)]
            on_model_edge = i in (0, nrow - 1) or j in (0, ncol - 1)
            touches_inactive = any(
                ni < 0 or ni >= nrow or nj < 0 or nj >= ncol or idomain[ni, nj] == 0
                for ni, nj in neighbors
            )
            if on_model_edge or touches_inactive:
                records.append(((0, i, j), float(strt[i, j])))
    return records


def zero_recharge_records(idomain: np.ndarray) -> list[tuple[tuple[int, int, int], float]]:
    """Create explicit zero recharge records so the RCH package is valid."""
    records = []
    nrow, ncol = idomain.shape
    for i in range(nrow):
        for j in range(ncol):
            if idomain[i, j] > 0:
                records.append(((0, i, j), 0.0))
    return records


def write_grid_products(grid: gpd.GeoDataFrame, output: Path) -> None:
    grid_path = output / "smalltest_modflow_grid.gpkg"
    active_path = output / "smalltest_modflow_grid_active.gpkg"
    csv_path = output / "smalltest_modflow_grid.csv"

    grid.to_file(grid_path, driver="GPKG")
    grid[grid["active"] == 1].to_file(active_path, driver="GPKG")
    grid.drop(columns="geometry").to_csv(csv_path, index=False)


def create_model(args: argparse.Namespace) -> dict[str, object]:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    domain = gpd.read_file(args.domain)
    if domain.crs is None:
        raise ValueError(f"Domain layer has no CRS: {args.domain}")

    hru_df = read_hru_table(args.swat_txt)
    grid, idomain2d, extent = build_grid(domain, args.cell_size, args.padding)
    nrow, ncol = idomain2d.shape
    idomain = idomain2d.reshape((1, nrow, ncol))

    target_crs = domain.crs.to_string()
    coeffs, elev_range = fit_surface_from_hrus(hru_df, target_crs)
    top, botm2d, strt2d = surface_arrays(
        coeffs,
        elev_range,
        extent,
        nrow,
        ncol,
        args.cell_size,
        args.aquifer_thickness,
        args.initial_depth,
    )
    botm = botm2d.reshape((1, nrow, ncol))
    strt = strt2d.reshape((1, nrow, ncol))
    chd_spd = perimeter_constant_heads(idomain2d, strt2d)
    rch_spd = zero_recharge_records(idomain2d)

    sim = flopy.mf6.MFSimulation(
        sim_name=MODEL_NAME,
        version="mf6",
        exe_name=str(args.mf6),
        sim_ws=str(output),
    )
    flopy.mf6.ModflowTdis(
        sim,
        time_units="DAYS",
        nper=1,
        perioddata=[(1.0, 1, 1.0)],
    )
    flopy.mf6.ModflowIms(
        sim,
        print_option="SUMMARY",
        complexity="SIMPLE",
        linear_acceleration="BICGSTAB",
        outer_dvclose=1e-4,
        inner_dvclose=1e-4,
        outer_maximum=100,
        inner_maximum=100,
    )
    gwf = flopy.mf6.ModflowGwf(
        sim,
        modelname=MODEL_NAME,
        save_flows=True,
        newtonoptions="NEWTON",
    )
    flopy.mf6.ModflowGwfdis(
        gwf,
        length_units="METERS",
        nlay=1,
        nrow=nrow,
        ncol=ncol,
        delr=args.cell_size,
        delc=args.cell_size,
        top=top,
        botm=botm,
        idomain=idomain,
        xorigin=extent[0],
        yorigin=extent[1],
    )
    flopy.mf6.ModflowGwfic(gwf, strt=strt)
    flopy.mf6.ModflowGwfnpf(
        gwf,
        icelltype=1,
        k=2.0,
        k33=0.2,
        save_specific_discharge=True,
    )
    flopy.mf6.ModflowGwfsto(
        gwf,
        iconvert=1,
        ss=1e-5,
        sy=0.12,
        steady_state={0: True},
    )
    flopy.mf6.ModflowGwfchd(
        gwf,
        stress_period_data={0: chd_spd},
        pname="CHD",
        save_flows=True,
    )
    flopy.mf6.ModflowGwfrch(
        gwf,
        stress_period_data={0: rch_spd},
        pname="RCH",
        save_flows=True,
    )
    flopy.mf6.ModflowGwfoc(
        gwf,
        budget_filerecord=f"{MODEL_NAME}.cbc",
        head_filerecord=f"{MODEL_NAME}.hds",
        saverecord=[("HEAD", "ALL"), ("BUDGET", "ALL")],
        printrecord=[("BUDGET", "LAST")],
    )

    sim.write_simulation()
    write_grid_products(grid, output)

    metadata = {
        "model_name": MODEL_NAME,
        "workspace": str(output),
        "domain": str(args.domain),
        "swat_txt": str(args.swat_txt),
        "crs": target_crs,
        "nlay": 1,
        "nrow": nrow,
        "ncol": ncol,
        "cell_size": args.cell_size,
        "active_cells": int(idomain2d.sum()),
        "constant_head_cells": len(chd_spd),
        "extent": {
            "xmin": extent[0],
            "ymin": extent[1],
            "xmax": extent[2],
            "ymax": extent[3],
        },
        "top_min": float(np.nanmin(top[idomain2d == 1])),
        "top_max": float(np.nanmax(top[idomain2d == 1])),
        "botm_min": float(np.nanmin(botm2d[idomain2d == 1])),
        "botm_max": float(np.nanmax(botm2d[idomain2d == 1])),
        "initial_head_min": float(np.nanmin(strt2d[idomain2d == 1])),
        "initial_head_max": float(np.nanmax(strt2d[idomain2d == 1])),
        "grid_files": [
            str(output / "smalltest_modflow_grid.gpkg"),
            str(output / "smalltest_modflow_grid_active.gpkg"),
            str(output / "smalltest_modflow_grid.csv"),
        ],
    }
    (output / "smalltest_modflow_metadata.json").write_text(json.dumps(metadata, indent=2))
    return metadata


def run_model(output: Path, mf6: Path) -> int:
    result = subprocess.run([str(mf6)], cwd=output, text=True)
    return result.returncode


def main() -> None:
    args = parse_args()
    metadata = create_model(args)
    print(f"Wrote MODFLOW 6 model: {metadata['workspace']}")
    print(
        "Grid: "
        f"{metadata['nlay']} layer, {metadata['nrow']} rows x {metadata['ncol']} cols, "
        f"{metadata['active_cells']} active cells"
    )
    print(f"CRS: {metadata['crs']}")
    print(f"Metadata: {Path(metadata['workspace']) / 'smalltest_modflow_metadata.json'}")

    if args.run:
        rc = run_model(Path(metadata["workspace"]), args.mf6)
        if rc != 0:
            raise SystemExit(rc)
        print("MODFLOW 6 run completed successfully.")


if __name__ == "__main__":
    main()
