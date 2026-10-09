"""Build a MODFLOW 6 simulation from GIS layers according to a :class:`BuildSpec`."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

from . import zones as zn
from .grid import (BuildInputError, Grid, overlap_mask, rasterize_geometries, read_grid, read_vector,
                   sample_raster_to_grid)
from .sfr import build_sfr, downstream_from_topology
from .spec import BuildSpec

log = logging.getLogger(__name__)
REPORT_FORMAT = "swatplus-modflow-build/1"


@dataclass
class BuildResult:
    sim_dir: Path
    report_path: Path
    warnings: List[str] = field(default_factory=list)
    report: dict = field(default_factory=dict)


def _nan_check(arr: np.ndarray, active: np.ndarray, what: str) -> None:
    bad = active & ~np.isfinite(arr)
    if bad.any():
        raise BuildInputError(f"{what}: {int(bad.sum())} active cell(s) have no value (raster no-data or "
                              "outside the raster extent)")


def _write_grid_layer(path: Path, grid: Grid, active2d: np.ndarray) -> None:
    """GeoPackage with one polygon per cell: integer row, col (1-based) and active (docs/DATA_CONTRACTS.md)."""
    import geopandas as gpd
    import shapely
    rr, cc = np.meshgrid(np.arange(grid.nrow), np.arange(grid.ncol), indexing="ij")
    rr, cc = rr.ravel(), cc.ravel()
    x0 = grid.xorigin + cc * grid.delr
    y1 = grid.ytop - rr * grid.delc
    gdf = gpd.GeoDataFrame({"row": rr + 1, "col": cc + 1, "active": active2d[rr, cc].astype(int)},
                           geometry=shapely.box(x0, y1 - grid.delc, x0 + grid.delr, y1), crs=grid.crs)
    gdf.to_file(path, layer="grid", driver="GPKG")


def build_modflow(spec: BuildSpec) -> BuildResult:
    import flopy.mf6 as mf6

    warnings: List[str] = []
    grid = read_grid(spec.grid)
    nrow, ncol, nlay = grid.nrow, grid.ncol, len(spec.layers)

    # ---- domain
    dom = read_vector(spec.domain.polygons.path, grid, spec.domain.polygons.layer)
    active2d = (overlap_mask(list(dom.geometry), grid) if spec.domain.rule == "overlap"
                else rasterize_geometries(list(dom.geometry), grid, all_touched=False))
    n_active2d = int(active2d.sum())
    if n_active2d == 0:
        raise BuildInputError("the domain polygon selects no grid cell (check CRS and extent)")

    # ---- top and bottoms
    top = sample_raster_to_grid(spec.top.dem.path, grid, spec.top.dem.resampling)
    _nan_check(top, active2d, "top (DEM)")
    botm = np.empty((nlay, nrow, ncol))
    prev = top
    thin_total = 0
    for k, lay in enumerate(spec.layers):
        if lay.thickness_m is not None:
            bot = prev - lay.thickness_m
        elif lay.bottom is not None:
            bot = sample_raster_to_grid(lay.bottom.path, grid, lay.bottom.resampling)
            _nan_check(bot, active2d, f"layer '{lay.name}' bottom")
        else:
            th = sample_raster_to_grid(lay.thickness.path, grid, lay.thickness.resampling)
            _nan_check(th, active2d, f"layer '{lay.name}' thickness")
            bot = prev - th
        thin = active2d & (prev - bot < spec.min_thickness_m)
        if thin.any():
            n = int(thin.sum())
            if spec.thin_cells == "error":
                raise BuildInputError(
                    f"layer '{lay.name}': {n} active cell(s) are thinner than min_thickness_m="
                    f"{spec.min_thickness_m:g} m (minimum found {float(np.nanmin((prev - bot)[active2d])):.3g} m). "
                    "Fix the inputs or set thin_cells: enforce")
            bot = np.where(thin, prev - spec.min_thickness_m, bot)
            thin_total += n
            warnings.append(f"layer '{lay.name}': {n} cell(s) thickened to min_thickness_m")
        botm[k] = bot
        prev = bot
    fill_top = float(np.nanmean(top[active2d]))
    top_f = np.where(active2d, top, fill_top)
    botm_f = np.where(active2d[None], botm, top_f[None] - np.arange(1, nlay + 1)[:, None, None] * spec.min_thickness_m)
    idomain = np.repeat(active2d[None].astype(int), nlay, axis=0)
    active3d = idomain > 0

    # ---- properties with explicit zone precedence
    P = spec.properties
    zone_inputs: Dict[str, zn.ZoneInput] = {}
    for z in P.zones:
        gdf = read_vector(z.polygons.path, grid, z.polygons.layer,
                          z.where.model_dump() if z.where else None)
        m2 = rasterize_geometries(list(gdf.geometry), grid, all_touched=False)
        lsel = np.zeros(nlay, dtype=bool)
        lsel[[l - 1 for l in z.layers] if z.layers else slice(None)] = True
        zone_inputs[z.name] = zn.ZoneInput(z.name, z.priority, m2[None] & lsel[:, None, None])
        if not (m2 & active2d).any():
            warnings.append(f"zone '{z.name}' covers no active cell (check CRS, extent and the where clause)")
    arrays, stats_all = {}, []
    default = {"k": P.default.k_m_per_day, "k33_over_k": P.default.k33_over_k, "sy": P.default.sy,
               "ss": P.default.ss_per_m}
    attr = {"k": "k_m_per_day", "k33_over_k": "k33_over_k", "sy": "sy", "ss": "ss_per_m"}
    problems: List[str] = []
    for prop in ("k", "k33_over_k", "sy", "ss"):
        zs = [z for z in P.zones if getattr(z, attr[prop]) is not None]
        zi = [zone_inputs[z.name] for z in zs]
        res = zn.resolve_property(prop, zi, active3d, P.rules.max_zone_share_percent)
        arrays[prop] = zn.apply_values(default[prop], zi, [getattr(z, attr[prop]) for z in zs], res)
        stats_all += res.stats
        problems += res.dominant
    if problems:
        if P.rules.on_dominant == "error":
            raise BuildInputError("zone precedence problem: " + "; ".join(problems))
        warnings += problems
    k = arrays["k"]
    k33 = k * arrays["k33_over_k"]
    zone_df = pd.DataFrame([dict(zone=s.zone, property=s.prop, priority=s.priority,
                                 cells_in_geometry=s.cells_in_geometry, cells_set=s.cells_set,
                                 cells_lost=s.cells_lost, cells_took_over=s.cells_took_over,
                                 share_of_active_percent=round(s.share_of_active_percent, 3),
                                 lost_to=json.dumps(s.lost_to, sort_keys=True)) for s in stats_all])
    for s in stats_all:
        if s.cells_lost and s.cells_set == 0:
            warnings.append(f"zone '{s.zone}' ({s.prop}) was entirely overridden by higher-priority zones; "
                            "it has no effect")
    uniform = {p: bool(np.ptp(arrays[p][active3d]) == 0) for p in arrays}
    if P.zones and uniform["k"]:
        warnings.append("hydraulic conductivity is uniform although zones were defined; check priorities")

    # ---- initial heads
    ih = spec.initial_heads
    if ih.mode == "below_top":
        strt = np.repeat((top_f - ih.depth_m)[None], nlay, axis=0)
    elif ih.mode == "constant":
        strt = np.full((nlay, nrow, ncol), ih.head_m)
    else:
        r = sample_raster_to_grid(ih.raster.path, grid, ih.raster.resampling)
        _nan_check(r, active2d, "initial heads raster")
        strt = np.repeat(np.where(active2d, r, fill_top)[None], nlay, axis=0)

    # ---- SFR
    sfr_build = None
    if spec.sfr is not None:
        s = spec.sfr
        cells = pd.read_csv(s.channel_cells)
        if s.topology.chandeg_con is not None:
            from ..engine.swat_io import read_connection_topology
            if s.topology.chandeg_con.name != "chandeg.con":
                raise BuildInputError("sfr.topology.chandeg_con must point at a file named chandeg.con")
            up, _ = read_connection_topology(s.topology.chandeg_con.parent)
            down = downstream_from_topology(up)
        else:
            t = pd.read_csv(s.topology.table)
            if not {"channel_id", "downstream_id"} <= set(t.columns):
                raise BuildInputError("topology table needs columns channel_id, downstream_id")
            down = {int(a): int(b) for a, b in zip(t["channel_id"], t["downstream_id"])}
        widths = None
        if s.channel_table is not None:
            ct = pd.read_csv(s.channel_table)
            if not {"channel_id", "width_m"} <= set(ct.columns):
                raise BuildInputError("channel_table needs columns channel_id, width_m")
            widths = {int(a): float(b) for a, b in zip(ct["channel_id"], ct["width_m"])}
        sfr_build = build_sfr(cells, down, grid, top_f, botm_f, idomain, incision_m=s.incision_m,
                              bed_thickness_m=s.bed_thickness_m, bed_k=s.bed_k_m_per_day,
                              mannings_n=s.mannings_n, width_m=s.width_m, widths=widths,
                              min_slope=s.min_slope, clearance_m=s.bed_clearance_m)
        warnings += sfr_build.notes

    # ---- boundaries
    bnd_data: Dict[str, list] = {}
    for b in spec.boundaries:
        gdf = read_vector(b.features.path, grid, b.features.layer, b.where.model_dump() if b.where else None)
        is_line = all(g.geom_type in ("LineString", "MultiLineString") for g in gdf.geometry)
        m = rasterize_geometries(list(gdf.geometry), grid, all_touched=is_line and b.line_buffer_cells)
        m &= active2d
        if not m.any():
            warnings.append(f"boundary '{b.name}' selects no active cell")
        rr, cc = np.where(m)
        elev = top_f[rr, cc] - b.head_below_top_m
        lay = b.layer - 1
        if b.type == "chd":
            bnd_data[b.name] = [[(lay, int(r), int(c)), float(e)] for r, c, e in zip(rr, cc, elev)]
        else:
            bnd_data[b.name] = [[(lay, int(r), int(c)), float(e), b.conductance_m2_per_day]
                                for r, c, e in zip(rr, cc, elev)]

    # ---- write MODFLOW 6
    out = spec.output.directory
    out.mkdir(parents=True, exist_ok=True)
    mname = spec.output.model_name
    sim = mf6.MFSimulation(sim_name="mfsim", sim_ws=str(out), exe_name="mf6")
    mf6.ModflowTdis(sim, time_units="DAYS", nper=1, perioddata=[(1.0, 1, 1.0)])
    gwf = mf6.ModflowGwf(sim, modelname=mname, save_flows=True, newtonoptions="NEWTON UNDER_RELAXATION")
    mf6.ModflowGwfdis(gwf, nlay=nlay, nrow=nrow, ncol=ncol, delr=grid.delr, delc=grid.delc, top=top_f,
                      botm=botm_f, idomain=idomain, length_units="METERS", xorigin=grid.xorigin,
                      yorigin=grid.yorigin)
    mf6.ModflowGwfic(gwf, strt=strt)
    mf6.ModflowGwfnpf(gwf, save_flows=True, icelltype=[int(l.convertible) for l in spec.layers], k=k, k33=k33)
    mf6.ModflowGwfsto(gwf, ss=arrays["ss"], sy=arrays["sy"], iconvert=[int(l.convertible) for l in spec.layers],
                      steady_state={0: True})
    mf6.ModflowGwfrcha(gwf, recharge=0.0)           # the engine sets recharge every day
    if spec.evt is not None:
        ev = spec.evt
        mf6.ModflowGwfevta(gwf, surface=top_f + ev.surface_offset_m, rate=np.where(active2d, ev.rate_m_per_day, 0.0),
                           depth=ev.extinction_depth_m)
    for b in spec.boundaries:
        data = bnd_data[b.name]
        if not data:
            continue
        pn = f"{b.type}_{b.name}".lower()
        if b.type == "chd":
            mf6.ModflowGwfchd(gwf, pname=pn, filename=f"{mname}.{pn}", stress_period_data={0: data})
        else:
            mf6.ModflowGwfdrn(gwf, pname=pn, filename=f"{mname}.{pn}", stress_period_data={0: data})
    if sfr_build is not None:
        mf6.ModflowGwfsfr(gwf, nreaches=len(sfr_build.packagedata), packagedata=sfr_build.packagedata,
                          connectiondata=sfr_build.connectiondata, perioddata={0: [[0, "RUNOFF", 0.0]]},
                          length_conversion=1.0, time_conversion=86400.0, save_flows=True)
        sfr_build.reaches.to_csv(out / "sfr_reaches.csv", index=False)
    mf6.ModflowIms(sim, complexity="COMPLEX")
    mf6.ModflowGwfoc(gwf, head_filerecord=f"{mname}.hds", budget_filerecord=f"{mname}.cbc",
                     saverecord=[("HEAD", "LAST"), ("BUDGET", "LAST")])
    sim.write_simulation(silent=True)
    (out / "grid.json").write_text(json.dumps(grid.to_json(), indent=2))
    if spec.output.write_grid_layer:
        _write_grid_layer(out / "grid_cells.gpkg", grid, active2d)
    zone_df.to_csv(out / "zone_report.csv", index=False)

    n_by_layer = [int(x) for x in active3d.sum(axis=(1, 2))]
    report = {
        "format": REPORT_FORMAT, "name": spec.name, "model_name": mname,
        "grid": grid.to_json(), "active_cells_per_layer": n_by_layer,
        "top_range_m": [float(np.nanmin(top[active2d])), float(np.nanmax(top[active2d]))],
        "thin_cells_enforced": thin_total,
        "property_ranges": {p: [float(arrays[p][active3d].min()), float(arrays[p][active3d].max())]
                            for p in ("k", "sy", "ss")},
        "zones": zone_df.to_dict(orient="records"),
        "sfr": sfr_build.stats if sfr_build is not None else None,
        "boundaries": {b.name: len(bnd_data[b.name]) for b in spec.boundaries},
        "warnings": warnings,
        "files": {"simulation": "mfsim.nam", "grid_layer": "grid_cells.gpkg" if spec.output.write_grid_layer else None, "sfr_reaches": "sfr_reaches.csv" if sfr_build is not None else None,
                  "zone_report": "zone_report.csv", "grid": "grid.json"},
    }
    rp = out / "build_report.json"
    rp.write_text(json.dumps(report, indent=2))
    return BuildResult(out, rp, warnings, report)
