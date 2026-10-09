"""Tests of the MODFLOW builder and the connection builders (synthetic layers only)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import build_fixtures as bf  # noqa: E402

from swatplus_modflow.build import connections as bc  # noqa: E402
from swatplus_modflow.build import zones as zn  # noqa: E402
from swatplus_modflow.build.grid import BuildInputError, read_grid  # noqa: E402
from swatplus_modflow.build.modflow_builder import build_modflow  # noqa: E402
from swatplus_modflow.build.sfr import build_sfr, downstream_from_topology  # noqa: E402
from swatplus_modflow.build.spec import load_spec  # noqa: E402
from swatplus_modflow.cli import main  # noqa: E402


# ---------------------------------------------------------------- zone precedence

def _zone_masks():
    nl, nr, nc = 1, 6, 6
    active = np.ones((nl, nr, nc), bool)
    a = np.zeros_like(active); a[0, 1:3, 1:3] = True            # 4 cells
    b = np.zeros_like(active); b[0, 3:5, 3:5] = True            # 4 cells
    regional = np.ones_like(active)                             # covers every cell
    return active, a, b, regional


def test_regional_class_drawn_last_no_longer_erases_finer_zones():
    """The research-builder bug: a regional class that covers every cell and is applied last made K uniform."""
    active, a, b, regional = _zone_masks()
    zi = [zn.ZoneInput("a", 10, a), zn.ZoneInput("b", 20, b), zn.ZoneInput("regional", 1, regional)]
    res = zn.resolve_property("k", zi, active, max_zone_share_percent=60)
    k = zn.apply_values(1.0, zi, [10.0, 0.05, 2.0], res)
    assert set(np.unique(k)) == {10.0, 0.05, 2.0}               # three distinct values, not one
    assert (k[a] == 10.0).all() and (k[b] == 0.05).all()
    stats = {s.zone: s for s in res.stats}
    assert stats["a"].cells_set == 4 and stats["a"].cells_lost == 0
    assert stats["regional"].cells_in_geometry == 36 and stats["regional"].cells_set == 28
    assert stats["regional"].cells_lost == 8 and stats["regional"].lost_to == {"a": 4, "b": 4}
    assert stats["regional"].cells_took_over == 0 and not res.dominant


def test_giving_the_regional_class_top_priority_is_detected():
    active, a, b, regional = _zone_masks()
    zi = [zn.ZoneInput("a", 10, a), zn.ZoneInput("b", 20, b), zn.ZoneInput("regional", 99, regional)]
    res = zn.resolve_property("k", zi, active, max_zone_share_percent=60)
    k = zn.apply_values(1.0, zi, [10.0, 0.05, 2.0], res)
    assert np.unique(k).tolist() == [2.0]                       # the old failure mode, now visible
    st = {s.zone: s for s in res.stats}
    assert st["a"].cells_set == 0 and st["a"].cells_lost == 4 and st["a"].lost_to == {"regional": 4}
    assert st["regional"].cells_took_over == 8
    assert len(res.dominant) == 1 and "regional" in res.dominant[0] and "100.0%" in res.dominant[0]


def test_a_dominant_background_class_that_overrides_nothing_is_not_flagged():
    active, a, b, regional = _zone_masks()
    zi = [zn.ZoneInput("regional", 1, regional)]
    res = zn.resolve_property("k", zi, active, 60)
    assert not res.dominant and res.stats[0].cells_took_over == 0


def test_properties_resolve_independently():
    active, a, b, regional = _zone_masks()
    only_sy = zn.ZoneInput("sy_zone", 50, regional)
    with_k = zn.ZoneInput("k_zone", 10, a)
    rk = zn.resolve_property("k", [with_k], active, 60)          # the sy-only zone is not a candidate for k
    assert (zn.apply_values(1.0, [with_k], [5.0], rk)[a] == 5.0).all()
    rs = zn.resolve_property("sy", [only_sy], active, 60)
    assert (zn.apply_values(0.1, [only_sy], [0.2], rs) == 0.2).all()


def test_equal_priorities_are_rejected():
    active, a, b, _ = _zone_masks()
    with pytest.raises(ValueError, match="unique"):
        zn.resolve_property("k", [zn.ZoneInput("x", 1, a), zn.ZoneInput("y", 1, b)], active, 60)


# ---------------------------------------------------------------- full build

def _build(tmp_path, **kw):
    bf.write_layers(tmp_path)
    spec = bf.spec_dict(**kw)
    p = bf.write_spec(tmp_path, spec)
    return load_spec(p)


def test_build_writes_a_loadable_model_with_distinct_k_zones(tmp_path):
    import flopy
    res = build_modflow(_build(tmp_path))
    rep = res.report
    assert rep["active_cells_per_layer"] == [bf.NROW * (bf.NCOL - 1)] * 2
    z = pd.read_csv(tmp_path / "model/zone_report.csv")
    k = z[z.property == "k"].set_index("zone")
    assert k.loc["a", "cells_set"] == 2 * 9 and k.loc["a", "cells_lost"] == 0     # 3x3 cells, 2 layers
    assert k.loc["regional", "cells_lost"] == 2 * (9 + 12)                        # 3x4 cells of zone b
    sim = flopy.mf6.MFSimulation.load(sim_ws=str(tmp_path / "model"), verbosity_level=0)
    gwf = sim.get_model("gwf")
    kk = np.asarray(gwf.npf.k.get_data())
    act = np.asarray(gwf.dis.idomain.get_data()) > 0
    assert sorted(np.unique(kk[act])) == [0.05, 2.0, 10.0]
    assert np.allclose(np.asarray(gwf.npf.k33.get_data())[act], 0.1 * kk[act])
    assert "uniform" not in " ".join(res.warnings)
    dis = gwf.dis
    assert dis.length_units.get_data().upper() == "METERS" and int(dis.nlay.get_data()) == 2
    bot = np.asarray(dis.botm.get_data()); top = np.asarray(dis.top.get_data())
    a2 = act[0]
    assert np.allclose((top - bot[0])[a2], 20.0, atol=1e-3) and np.allclose((bot[0] - bot[1])[a2], 40.0, atol=1e-3)


def test_build_fails_when_a_regional_zone_with_top_priority_overrides_finer_zones(tmp_path, capsys):
    bf.write_layers(tmp_path)
    p = bf.write_spec(tmp_path, bf.spec_dict(zone_priorities=(10, 20, 99)))
    assert main(["build-modflow", str(p)]) == 1
    out = capsys.readouterr().out
    assert "ERROR: zone precedence problem" in out and "regional" in out
    assert not (tmp_path / "model" / "mfsim.nam").exists()
    spec = bf.spec_dict(zone_priorities=(10, 20, 99))
    spec["properties"]["rules"] = {"on_dominant": "warn"}
    p = bf.write_spec(tmp_path, spec)
    assert main(["build-modflow", str(p)]) == 0
    out = capsys.readouterr().out
    assert "WARNING: zone 'regional'" in out and "uniform" in out and "entirely overridden" in out


def test_duplicate_priorities_fail_validation(tmp_path, capsys):
    bf.write_layers(tmp_path)
    p = bf.write_spec(tmp_path, bf.spec_dict(zone_priorities=(5, 5, 1)))
    assert main(["build-modflow", str(p)]) == 1
    assert "priorities must be unique" in capsys.readouterr().out


def test_unknown_key_and_wrong_version_are_rejected(tmp_path, capsys):
    bf.write_layers(tmp_path)
    s = bf.spec_dict(); s["surprise"] = 1
    assert main(["build-modflow", str(bf.write_spec(tmp_path, s))]) == 1
    assert "surprise" in capsys.readouterr().out
    s = bf.spec_dict(); s["build_version"] = 2
    assert main(["build-modflow", str(bf.write_spec(tmp_path, s))]) == 1


def test_crs_mismatch_is_an_error(tmp_path):
    import geopandas as gpd
    bf.write_layers(tmp_path)
    g = gpd.read_file(tmp_path / "domain.gpkg").set_crs("EPSG:4326", allow_override=True)
    g.to_file(tmp_path / "domain.gpkg", driver="GPKG")
    with pytest.raises(BuildInputError, match="differs from the grid CRS"):
        build_modflow(load_spec(bf.write_spec(tmp_path, bf.spec_dict())))


def test_thin_cells_error_or_enforce(tmp_path):
    bf.write_layers(tmp_path)
    s = bf.spec_dict(); s["layers"][0]["thickness_m"] = 0.5
    with pytest.raises(BuildInputError, match="thinner than min_thickness_m"):
        build_modflow(load_spec(bf.write_spec(tmp_path, s)))
    s["thin_cells"] = "enforce"
    res = build_modflow(load_spec(bf.write_spec(tmp_path, s)))
    assert res.report["thin_cells_enforced"] == bf.NROW * (bf.NCOL - 1)


def test_dem_nodata_in_domain_is_an_error(tmp_path):
    bf.write_layers(tmp_path)
    dem = bf.dem_array(); dem[4:6, 4:6] = -9999.0
    bf.write_raster(tmp_path / "dem.tif", dem)
    with pytest.raises(BuildInputError, match="no value"):
        build_modflow(load_spec(bf.write_spec(tmp_path, bf.spec_dict())))


# ---------------------------------------------------------------- connections

def test_hru_cells_partly_outside_grid(tmp_path):
    import geopandas as gpd
    bf.write_layers(tmp_path)
    grid = read_grid(tmp_path / "grid.json")
    hrus = gpd.read_file(tmp_path / "hrus.gpkg")
    df, rep = bc.build_hru_cells(hrus, grid)
    assert list(df.columns) == ["hru_id", "layer", "row", "col", "area_m2", "fraction"]
    assert df["row"].min() == 1 and df["col"].min() == 1                       # 1-based
    f = df.groupby("hru_id")["fraction"].sum()
    assert f[1] == pytest.approx(1.0) and f[2] == pytest.approx(1.0)
    # HRU 3 spans 6..14 cell-columns but the grid has 12: 6 of 8 columns inside
    assert f[3] == pytest.approx(6 / 8)
    assert rep["n_hrus_partly_outside"] == 1 and rep["area_outside_grid_m2"] == pytest.approx(
        0.25 * bf.NROW * bf.CELL * 8 * bf.CELL)
    assert df.groupby("hru_id")["area_m2"].sum()[1] == pytest.approx(5 * 6 * bf.CELL ** 2)
    # active-cell mask removes cells and shrinks the fractions (reported, not rescaled)
    active = np.ones((bf.NROW, bf.NCOL), bool); active[:, 0] = False
    df2, _ = bc.build_hru_cells(hrus, grid, active=active)
    assert df2.groupby("hru_id")["fraction"].sum()[1] == pytest.approx(5 / 6)


def test_hru_field_name_is_a_parameter_and_must_exist(tmp_path):
    import geopandas as gpd
    bf.write_layers(tmp_path)
    grid = read_grid(tmp_path / "grid.json")
    hrus = gpd.read_file(tmp_path / "hrus.gpkg").rename(columns={"HRUS": "my_id"})
    with pytest.raises(BuildInputError, match="no field 'HRUS'"):
        bc.build_hru_cells(hrus, grid)
    df, _ = bc.build_hru_cells(hrus, grid, id_field="my_id")
    assert set(df.hru_id) == {1, 2, 3}


def test_hru_layer_crs_must_match(tmp_path):
    import geopandas as gpd
    bf.write_layers(tmp_path)
    grid = read_grid(tmp_path / "grid.json")
    hrus = gpd.read_file(tmp_path / "hrus.gpkg").set_crs("EPSG:4326", allow_override=True)
    with pytest.raises(BuildInputError, match="differs from the grid CRS"):
        bc.build_hru_cells(hrus, grid)
    nocrs = gpd.read_file(tmp_path / "hrus.gpkg")
    nocrs.crs = None
    with pytest.raises(BuildInputError, match="no CRS"):
        bc.build_hru_cells(nocrs, grid)


def test_channel_cells_order_lengths_and_directions(tmp_path):
    import geopandas as gpd
    bf.write_layers(tmp_path)
    grid = read_grid(tmp_path / "grid.json")
    ch = gpd.read_file(tmp_path / "channels.gpkg")
    df, rep = bc.build_channel_cells(ch, grid)
    c1 = df[df.channel_id == 1].sort_values("seq")
    assert list(c1["seq"]) == list(range(1, len(c1) + 1))
    assert c1["col"].is_monotonic_increasing and (c1["row"] == 4).all()         # y = 6.5 cells above the south edge of a 10-row grid -> row 4 from the north
    assert c1["length_m"].sum() == pytest.approx(8.5 * bf.CELL - 10)
    assert c1["fraction"].sum() == pytest.approx(1.0)
    c2 = df[df.channel_id == 2].sort_values("seq")
    assert c2["row"].is_monotonic_increasing                                    # drawn from row 4 southward
    rev, _ = bc.build_channel_cells(ch, grid, direction="reversed")
    r2 = rev[rev.channel_id == 2].sort_values("seq")
    assert r2["row"].is_monotonic_decreasing
    dem = np.arange(bf.NROW)[:, None] * np.ones((1, bf.NCOL))                  # ground rises toward the south
    d, _ = bc.build_channel_cells(ch, grid, direction="dem", dem=dem)
    assert d[d.channel_id == 2].sort_values("seq")["row"].is_monotonic_decreasing   # so water runs north
    dem_down = dem[::-1]
    d2, _ = bc.build_channel_cells(ch, grid, direction="dem", dem=dem_down)
    assert d2[d2.channel_id == 2].sort_values("seq")["row"].is_monotonic_increasing
    with pytest.raises(BuildInputError, match="duplicate"):
        bc.build_channel_cells(pd.concat([ch, ch]), grid)


def test_connection_cli_writes_contract_files(tmp_path, capsys):
    bf.write_layers(tmp_path)
    out = tmp_path / "conn"
    code = main(["build-connections", "--grid", str(tmp_path / "grid.json"), "--hrus", str(tmp_path / "hrus.gpkg"),
                 "--channels", str(tmp_path / "channels.gpkg"), "--out-dir", str(out)])
    assert code == 0
    text = capsys.readouterr().out
    assert "WARNING: 1 HRU(s) are partly outside the grid" in text and text.splitlines()[-1].startswith("RESULT succeeded")
    assert list(pd.read_csv(out / "hru_cells.csv").columns) == ["hru_id", "layer", "row", "col", "area_m2", "fraction"]
    assert list(pd.read_csv(out / "channel_cells.csv").columns) == [
        "channel_id", "seq", "layer", "row", "col", "length_m", "fraction"]
    rep = json.loads((out / "connections_report.json").read_text())
    assert rep["hru_cells"]["format"] == "swatplus-modflow-hru-cells/1"
    assert main(["build-connections", "--grid", str(tmp_path / "grid.json"), "--hrus", str(tmp_path / "hrus.gpkg"),
                 "--crs", "EPSG:4326", "--out-dir", str(out)]) == 1


# ---------------------------------------------------------------- SFR

def test_sfr_topology_numbering_and_connections():
    from swatplus_modflow.build.grid import Grid
    g = Grid(bf.CRS, bf.X0, bf.Y0, bf.CELL, bf.CELL, bf.NROW, bf.NCOL)
    cells = pd.DataFrame({
        "channel_id": [5, 5, 5, 2, 2], "seq": [1, 2, 3, 1, 2], "layer": 1,
        "row": [2, 2, 2, 3, 4], "col": [1, 2, 3, 3, 3], "length_m": 50.0})
    top = np.full((g.nrow, g.ncol), 50.0)
    botm = np.stack([top - 20.0, top - 60.0])
    idom = np.ones((2, g.nrow, g.ncol), int)
    sb = build_sfr(cells, {5: 2, 2: 0}, g, top, botm, idom, incision_m=1.0, bed_thickness_m=1.0, bed_k=1.0,
                   mannings_n=0.03, width_m=3.0, widths=None, min_slope=1e-4, clearance_m=0.01)
    r = sb.reaches
    assert list(r.channel_id) == [5, 5, 5, 2, 2] and list(r.rno) == [1, 2, 3, 4, 5]      # upstream channel first
    assert all(c[0] >= 0 for c in sb.connectiondata)
    con = {c[0]: c[1:] for c in sb.connectiondata}
    assert con[1] == [0, -2] and con[2] == [1, -3]
    pkg = {p[0]: p for p in sb.packagedata}
    assert pkg[2][9] == len(con[2])                                       # ncon matches the listed connections
    assert sb.stats["n_links"] == 1
    # channel 5's outlet (0-based reach 2) connects to the nearest reach of channel 2 (reach 3)
    assert -3 in con[2] and 2 in con[3]
    assert all(pkg[i][5] >= pkg[i + 1][5] for i in (0, 1))                # bed never rises downstream
    with pytest.raises(BuildInputError, match="cycle"):
        build_sfr(cells, {5: 2, 2: 5}, g, top, botm, idom, incision_m=1.0, bed_thickness_m=1.0, bed_k=1.0,
                  mannings_n=0.03, width_m=3.0, widths=None, min_slope=1e-4, clearance_m=0.01)


def test_downstream_through_reservoir():
    up = {-1: [1], 3: [-1], 4: [3]}
    assert downstream_from_topology(up) == {1: 3, 3: 4, 4: 0}


def test_inactive_channel_pieces_are_dropped_and_reported():
    from swatplus_modflow.build.grid import Grid
    g = Grid(bf.CRS, bf.X0, bf.Y0, bf.CELL, bf.CELL, bf.NROW, bf.NCOL)
    cells = pd.DataFrame({"channel_id": [1, 1], "seq": [1, 2], "layer": 1, "row": [1, 2], "col": [1, 1],
                          "length_m": [50.0, 50.0]})
    top = np.full((g.nrow, g.ncol), 50.0)
    botm = np.stack([top - 20.0, top - 60.0])
    idom = np.ones((2, g.nrow, g.ncol), int); idom[:, 0, 0] = 0
    sb = build_sfr(cells, {1: 0}, g, top, botm, idom, incision_m=1.0, bed_thickness_m=1.0, bed_k=1.0,
                   mannings_n=0.03, width_m=3.0, widths=None, min_slope=1e-4, clearance_m=0.01)
    assert len(sb.reaches) == 1 and any("inactive" in n for n in sb.notes)
