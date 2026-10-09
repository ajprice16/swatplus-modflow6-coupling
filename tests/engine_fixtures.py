"""Synthetic project builders shared by the engine tests.

Everything here is invented: a 2-layer, 6 x 8 grid with 100 m cells, three HRUs,
two channels. No real watershed, gauge or coordinate is used.
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import yaml

NLAY, NROW, NCOL = 2, 6, 8
CELL = 100.0
SFR_COL = 4            # 0-based column of the stream
HRU_CON_AREA_HA = {1: 8.0, 2: 6.0, 3: 5.0}   # ha


# ----------------------------------------------------------------- libmf6 discovery

def _lib_names():
    if sys.platform == "win32":
        return ["libmf6.dll"]
    return ["libmf6.dylib"] if sys.platform == "darwin" else ["libmf6.so"]


LIB_REQUIRED = bool(os.environ.get("SWATPLUS_REQUIRE_LIBMF6"))


def find_libmf6() -> Path | None:
    """libmf6 from $SWATPLUS_TEST_LIBMF6, else common install folders, else None."""
    def exists(p: Path) -> bool:
        try:
            return p.exists()
        except OSError:
            return False

    env = os.environ.get("SWATPLUS_TEST_LIBMF6")
    if env and exists(Path(env)):
        return Path(env)
    exe = Path(sys.executable).resolve().parent
    dirs = [exe.parent / "lib", exe, exe.parent / "Library" / "bin", Path(sys.prefix) / "lib", Path(sys.prefix) / "bin", Path(sys.prefix) / "Library" / "bin",
            Path.home() / ".local" / "share" / "flopy" / "bin", Path.cwd() / "bin"]
    dirs += [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    for d in dirs:
        for n in _lib_names():
            if exists(d / n):
                return d / n
    return None


# ----------------------------------------------------------------- MODFLOW model

def build_modflow(sim_dir: Path, *, length_units: str = "METERS", steady: bool = True,
                  with_evt: bool = True, with_wel: bool = True, extra_period: bool = False,
                  rch_value: float = 0.0, newton: bool = True) -> dict:
    import flopy.mf6 as mf6
    sim_dir.mkdir(parents=True, exist_ok=True)
    sim = mf6.MFSimulation(sim_name="sim", sim_ws=str(sim_dir), exe_name="mf6")
    mf6.ModflowTdis(sim, time_units="DAYS", nper=2 if extra_period else 1,
                    perioddata=[(1.0, 1, 1.0)] * (2 if extra_period else 1))
    gwf = mf6.ModflowGwf(sim, modelname="gwf", save_flows=True,
                         newtonoptions="NEWTON UNDER_RELAXATION" if newton else None)
    idomain = np.ones((NLAY, NROW, NCOL), dtype=int)
    idomain[:, 0, 0] = 0                                       # one inactive column
    mf6.ModflowGwfdis(gwf, nlay=NLAY, nrow=NROW, ncol=NCOL, delr=CELL, delc=CELL, top=20.0,
                      botm=[10.0, 0.0], idomain=idomain, length_units=length_units,
                      xorigin=0.0, yorigin=0.0)
    mf6.ModflowGwfic(gwf, strt=15.0)
    mf6.ModflowGwfnpf(gwf, save_flows=True, icelltype=1, k=[2.0, 1.0])
    mf6.ModflowGwfsto(gwf, ss=1e-5, sy=0.1, iconvert=1,
                      steady_state={0: True} if steady else None, transient=None if steady else {0: True})
    mf6.ModflowGwfrcha(gwf, recharge=rch_value)
    if with_evt:
        mf6.ModflowGwfevta(gwf, surface=20.0, rate=2.0e-4, depth=3.0)
    if with_wel:
        mf6.ModflowGwfwel(gwf, stress_period_data={0: [[(0, 3, 2), -40.0]]})
    # channel 1 = rows 1..3, channel 2 = rows 4..6 (1-based), stream in column SFR_COL
    nr = NROW
    conn = []
    for i in range(nr):
        c = [i]
        if i > 0:
            c.append(i - 1)
        if i < nr - 1:
            c.append(-(i + 1))
        conn.append(c)
    pkd = [[i, (0, i, SFR_COL), CELL, 3.0, 0.001, 18.0, 1.0, 1.0e-2, 0.035, len(conn[i]) - 1, 1.0, 0]
           for i in range(nr)]
    mf6.ModflowGwfsfr(gwf, nreaches=nr, packagedata=pkd, connectiondata=conn,
                      perioddata={0: [[0, "RUNOFF", 0.0]]}, length_conversion=1.0,
                      time_conversion=86400.0, save_flows=True)
    mf6.ModflowIms(sim, complexity="COMPLEX")
    mf6.ModflowGwfoc(gwf, head_filerecord="gwf.hds", saverecord=[("HEAD", "LAST")])
    sim.write_simulation(silent=True)
    return dict(nlay=NLAY, nrow=NROW, ncol=NCOL)


# ----------------------------------------------------------------- connection tables

def write_connections(conn_dir: Path, *, offgrid_hru: bool = True) -> None:
    conn_dir.mkdir(parents=True, exist_ok=True)
    cell = CELL * CELL
    rows = ["hru_id,layer,row,col,area_m2,fraction"]

    def add(h, r, c, frac):
        rows.append(f"{h},1,{r},{c},{HRU_CON_AREA_HA[h] * 1e4 * frac:.3f},{frac:.6f}")

    add(1, 2, 2, 0.5); add(1, 2, 3, 0.5)                     # HRU 1 spans two cells
    add(2, 4, 6, 0.25); add(2, 5, 6, 0.25); add(2, 4, 7, 0.5)
    if offgrid_hru:
        add(3, 6, 8, 0.8)                                    # HRU 3: 20 % outside the grid
    else:
        add(3, 6, 8, 1.0)
    (conn_dir / "hru_cells.csv").write_text("\n".join(rows) + "\n")
    reaches = ["rno,channel_id,seq,layer,row,col,length_m"]
    for i in range(NROW):
        ch = 1 if i < 3 else 2
        seq = i + 1 if i < 3 else i - 2
        reaches.append(f"{i + 1},{ch},{seq},1,{i + 1},{SFR_COL + 1},{CELL}")
    (conn_dir / "sfr_reaches.csv").write_text("\n".join(reaches) + "\n")


# ----------------------------------------------------------------- SWAT+ files

HRU_WB_COLS = ["jday", "mon", "day", "yr", "unit", "gis_id", "name", "precip", "snofall", "snomlt",
               "surq_gen", "latq", "wateryld", "perc", "et"]
HRU_WB_UNITS = ["", "", "", "", "", "", "", "mm", "mm", "mm", "mm", "mm", "mm", "mm", "mm"]
CH_COLS = ["jday", "mon", "day", "yr", "unit", "gis_id", "name", "area", "precip", "evap", "seep",
           "flo_stor", "flo_in", "flo_out"]
CH_UNITS = ["", "", "", "", "", "", "", "ha", "ha-m", "m^3", "m^3", "m^3", "m^3/s", "m^3/s"]


def perc_series(n_days: int, hru: int) -> np.ndarray:
    d = np.arange(n_days)
    return np.where(d % 5 == (hru % 5), 4.0 + hru, 0.2 * hru)       # mm/day


def channel_series(n_days: int):
    d = np.arange(n_days)
    local1 = 0.10 + 0.05 * (d % 4)           # m3/s generated in channel 1
    local2 = 0.05 + 0.02 * (d % 3)
    seep1 = 20.0 + 0.0 * d                   # m3/day
    seep2 = 10.0 + 0.0 * d
    out1 = local1
    out2 = out1 + local2
    return out1, out2, seep1, seep2


def write_swatplus(txt: Path, start: date, n_days: int, *, csv: bool = False, with_seep: bool = True,
                   perc_scale: float = 1.0, end_trim: int = 0) -> None:
    txt.mkdir(parents=True, exist_ok=True)
    hcon = ["hru.con: synthetic", "id name gis_id area lat lon elev hru wst cst ovfl rule out_tot obj_typ obj_id hyd_typ frac"]
    for h, ha in HRU_CON_AREA_HA.items():
        hcon.append(f"{h} hru{h:02d} {h} {ha} 0.0 0.0 0.0 {h} wst1 0 0 0 1 sdc {1 if h < 3 else 2} tot 1.0")
    (txt / "hru.con").write_text("\n".join(hcon) + "\n")
    ccon = ["chandeg.con: synthetic",
            "id name gis_id area lat lon elev lcha wst cst ovfl rule out_tot obj_typ obj_id hyd_typ frac",
            "1 cha01 1 10.0 0.0 0.0 0.0 1 wst1 0 0 0 1 sdc 2 tot 1.0",
            "2 cha02 2 20.0 0.0 0.0 0.0 2 wst1 0 0 0 0"]
    (txt / "chandeg.con").write_text("\n".join(ccon) + "\n")
    (txt / "time.sim").write_text("time.sim: synthetic\nday_start yrc_start day_end yrc_end step\n"
                                  f"{start.timetuple().tm_yday} {start.year} 0 {start.year + 2} 0\n")
    n = n_days - end_trim
    days = [start + timedelta(days=i) for i in range(n)]
    sep = "," if csv else " "
    ext = "csv" if csv else "txt"

    def fmt(vals):
        return sep.join(str(v) for v in vals)

    lines = ["hru_wb_day synthetic", fmt(HRU_WB_COLS), fmt(HRU_WB_UNITS if not csv else [u or "-" for u in HRU_WB_UNITS])]
    perc = {h: perc_series(n_days, h) * perc_scale for h in HRU_CON_AREA_HA}
    for i, d in enumerate(days):
        for h in HRU_CON_AREA_HA:
            lines.append(fmt([d.timetuple().tm_yday, d.month, d.day, d.year, h, h, f"hru{h:02d}", 5.0, 0.0,
                              0.0, 1.0, 0.5, 1.5, f"{perc[h][i]:.6f}", 2.0]))
    (txt / f"hru_wb_day.{ext}").write_text("\n".join(lines) + "\n")
    out1, out2, seep1, seep2 = channel_series(n_days)
    lines = ["channel_sd_day synthetic", fmt([c for c in CH_COLS if with_seep or c != "seep"]),
             fmt([u or "-" for u in CH_UNITS] if csv else CH_UNITS) if with_seep else
             fmt([u for c, u in zip(CH_COLS, CH_UNITS) if c != "seep"])]
    for i, d in enumerate(days):
        for ch, q, sp in ((1, out1[i], seep1[i]), (2, out2[i], seep2[i])):
            row = [d.timetuple().tm_yday, d.month, d.day, d.year, ch, ch, f"cha{ch:02d}", 10.0, 0.0, 1.0]
            if with_seep:
                row.append(sp)
            row += [100.0, f"{q:.6f}", f"{q:.6f}"]
            lines.append(fmt(row))
    (txt / f"channel_sd_day.{ext}").write_text("\n".join(lines) + "\n")


# ----------------------------------------------------------------- project

def make_project(root: Path, libmf6: Path | None, *, start=date(2001, 1, 1), n_days=45, spinup=0,
                 run_kwargs: dict | None = None, modflow_kwargs: dict | None = None,
                 swat_kwargs: dict | None = None, coupling: dict | None = None,
                 checks: dict | None = None, output: dict | None = None,
                 conn_kwargs: dict | None = None, solver: dict | None = None,
                 swat_start_offset: int = 0) -> Path:
    """Write a complete project under ``root`` and return the project.yaml path."""
    root.mkdir(parents=True, exist_ok=True)
    build_modflow(root / "modflow", **(modflow_kwargs or {}))
    write_connections(root / "connections", **(conn_kwargs or {}))
    sim_start = start - timedelta(days=spinup)
    total = (n_days + spinup)
    write_swatplus(root / "TxtInOut", sim_start - timedelta(days=swat_start_offset), total + swat_start_offset,
                   **(swat_kwargs or {}))
    cfg = {
        "schema_version": 1, "name": "synthetic-test", "crs": "EPSG:3857",
        "swatplus": {"txtinout": "TxtInOut", "run_swatplus": False},
        "modflow": {"sim_dir": "modflow", "libmf6": str(libmf6) if libmf6 else "libmf6-missing",
                    "solver": solver or {}},
        "connections": {"hru_cells": "connections/hru_cells.csv",
                        "channel_reaches": "connections/sfr_reaches.csv"},
        "period": {"start": start.isoformat(), "end": (start + timedelta(days=n_days - 1)).isoformat(),
                   "spinup_days": spinup},
        "coupling": {"recharge_update": "daily", **(coupling or {})},
        "checks": checks or {},
        "output": {"directory": "results", **(output or {})},
    }
    p = root / "project.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return p


def write_head_file(path: Path, layers) -> None:
    """Write a MODFLOW 6 style (double precision) binary head file with one record per layer."""
    import struct
    with open(path, "wb") as fh:
        for k, arr in enumerate(layers):
            arr = np.asarray(arr, dtype=np.float64)
            nrow, ncol = arr.shape
            fh.write(struct.pack("<iidd", 1, 1, 1.0, 1.0))                 # kstp, kper, pertim, totim
            fh.write(b"            HEAD")
            fh.write(struct.pack("<iii", ncol, nrow, k + 1))
            fh.write(arr.tobytes())
