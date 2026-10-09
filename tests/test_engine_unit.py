"""Unit tests of the daily engine on synthetic arrays and files (no libmf6 needed)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import engine_fixtures as fx  # noqa: E402

from swatplus_modflow.engine import budget, connections_io as cio, forcing, mf6_prepare, swat_io  # noqa: E402
from swatplus_modflow.engine import transmission  # noqa: E402
from swatplus_modflow.engine.driver import _water_table_nodes  # noqa: E402
from swatplus_modflow.engine.errors import EngineInputError  # noqa: E402


# ------------------------------------------------------------------ SWAT+ readers

@pytest.mark.parametrize("csv", [False, True])
def test_daily_matrix_by_header_name(tmp_path, csv):
    fx.write_swatplus(tmp_path, date(2001, 1, 1), 10, csv=csv)
    f = swat_io.find_output(tmp_path, "hru_wb_day")
    mats, ids = swat_io.read_daily_matrix(f, ["perc"], [1, 2, 3], date(2001, 1, 3), date(2001, 1, 6))
    assert ids == [1, 2, 3] and mats["perc"].shape == (4, 3)
    assert np.allclose(mats["perc"][:, 0], fx.perc_series(10, 1)[2:6])
    assert swat_io.output_date_range(f) == (date(2001, 1, 1), date(2001, 1, 10))


def test_channel_units_and_missing_column(tmp_path):
    fx.write_swatplus(tmp_path, date(2001, 1, 1), 5)
    f = swat_io.find_output(tmp_path, "channel_sd_day")
    m, _ = swat_io.read_daily_matrix(f, ["flo_out", "seep"], [1, 2], date(2001, 1, 1), date(2001, 1, 5),
                                     factors={"flo_out": swat_io.UNIT_TABLES["flo_out"],
                                              "seep": swat_io.UNIT_TABLES["seep"]})
    o1, o2, s1, s2 = fx.channel_series(5)
    assert np.allclose(m["flo_out"][:, 1], o2) and np.allclose(m["seep"][:, 0], s1)
    with pytest.raises(EngineInputError, match="not found"):
        swat_io.read_daily_matrix(f, ["no_such_column"], [1], date(2001, 1, 1), date(2001, 1, 2))


def test_unknown_unit_is_an_error_not_a_guess(tmp_path):
    fx.write_swatplus(tmp_path, date(2001, 1, 1), 3)
    f = tmp_path / "hru_wb_day.txt"
    f.write_text(f.read_text().replace(" mm mm mm mm mm mm mm\n", " mm mm mm mm mm in mm\n", 1))
    with pytest.raises(EngineInputError, match="does not convert"):
        swat_io.read_daily_matrix(f, ["perc"], [1], date(2001, 1, 1), date(2001, 1, 3),
                                  factors={"perc": swat_io.UNIT_TABLES["perc"]})


def test_ha_m_volume_is_converted(tmp_path):
    fx.write_swatplus(tmp_path, date(2001, 1, 1), 3)
    f = tmp_path / "channel_sd_day.txt"
    f.write_text(f.read_text().replace("ha ha-m m^3 m^3 m^3 m^3/s m^3/s", "ha ha-m m^3 ha-m m^3 m^3/s m^3/s", 1))
    m, _ = swat_io.read_daily_matrix(f, ["seep"], [1], date(2001, 1, 1), date(2001, 1, 3),
                                     factors={"seep": swat_io.UNIT_TABLES["seep"]})
    assert np.allclose(m["seep"][:, 0], 20.0 * 1.0e4)


def test_duplicate_and_gap_detection(tmp_path):
    fx.write_swatplus(tmp_path, date(2001, 1, 1), 4)
    f = tmp_path / "hru_wb_day.txt"
    lines = f.read_text().splitlines()
    f.write_text("\n".join(lines + [lines[3]]) + "\n")
    with pytest.raises(EngineInputError, match="duplicate"):
        swat_io.read_daily_matrix(f, ["perc"], [1], date(2001, 1, 1), date(2001, 1, 4))
    fx.write_swatplus(tmp_path, date(2001, 1, 1), 4, end_trim=1)
    mats, _ = swat_io.read_daily_matrix(f, ["perc"], [1], date(2001, 1, 1), date(2001, 1, 4))
    assert np.isnan(mats["perc"][3, 0])                    # a gap is NaN, never silently zero


def test_hru_areas_hectares_to_m2(tmp_path):
    fx.write_swatplus(tmp_path, date(2001, 1, 1), 2)
    a = swat_io.read_hru_areas(tmp_path)
    assert a == {1: 8.0e4, 2: 6.0e4, 3: 5.0e4}


def test_topology_with_reservoir_and_multiple_outflows(tmp_path):
    (tmp_path / "chandeg.con").write_text(
        "t\nid name gis_id area lat lon elev lcha wst cst ovfl rule out_tot obj_typ obj_id hyd_typ frac\n"
        "1 a 1 1 0 0 0 1 w 0 0 0 1 res 1 tot 1.0\n"
        "2 b 2 1 0 0 0 2 w 0 0 0 1 res 1 tot 1.0\n"
        "3 c 3 1 0 0 0 3 w 0 0 0 1 sdc 4 tot 1.0\n"
        "4 d 4 1 0 0 0 4 w 0 0 0 2 sdc 5 tot 0.5 sdc 6 tot 0.5\n"
        "5 e 5 1 0 0 0 5 w 0 0 0 0\n6 f 6 1 0 0 0 6 w 0 0 0 0\n")
    (tmp_path / "reservoir.con").write_text(
        "t\nid name gis_id area lat lon elev res wst cst ovfl rule out_tot obj_typ obj_id hyd_typ frac\n"
        "1 r 1 1 0 0 0 1 w 0 0 0 1 sdc 3 tot 1.0\n")
    up, ids = swat_io.read_connection_topology(tmp_path)
    assert ids == [1, 2, 3, 4, 5, 6]
    assert sorted(up[-1]) == [1, 2] and up[3] == [-1] and up[5] == [4] and up[6] == [4]
    # channel 3 sits below a reservoir fed by channels 1 and 2 -> nearest upstream with output
    assert sorted(swat_io.upstream_with_output(3, up, {1, 2, 3, 4, 5, 6})) == [1, 2]
    # if channel 1 has no output, its own upstream (none) replaces it
    assert swat_io.upstream_with_output(3, up, {2, 3}) == [2]


def test_time_sim_reading(tmp_path):
    fx.write_swatplus(tmp_path, date(2001, 3, 1), 3)
    assert swat_io.read_time_sim(tmp_path) == (date(2001, 3, 1), date(2003, 12, 31))


# ------------------------------------------------------------------ connection tables

def test_hru_cell_diagnostics(tmp_path):
    fx.write_connections(tmp_path)
    hc = cio.read_hru_cells(tmp_path / "hru_cells.csv", (2, 6, 8))
    areas = {h: a * 1e4 for h, a in fx.HRU_CON_AREA_HA.items()}
    errs, warns = cio.hru_cell_diagnostics(hc, areas)
    assert not errs
    assert any("less than 1" in w for w in warns)                    # HRU 3 partly outside the grid
    assert not any("polygon area differs" in w for w in warns)       # whole-polygon area matches hru.con
    errs, warns = cio.hru_cell_diagnostics(hc, {**areas, 1: areas[1] * 1.2})
    assert any("polygon area differs" in w and "5%" in w for w in warns)
    errs, _ = cio.hru_cell_diagnostics(hc, {1: 1.0})
    assert any("not in hru.con" in e for e in errs)
    bad = tmp_path / "bad.csv"
    bad.write_text("hru_id,layer,row,col,area_m2,fraction\n1,1,2,2,100,0.7\n1,1,2,3,100,0.7\n")
    errs, _ = cio.hru_cell_diagnostics(cio.read_hru_cells(bad), {1: 200.0})
    assert any("more than 1" in e for e in errs)


def test_index_validation_is_one_based(tmp_path):
    p = tmp_path / "h.csv"
    p.write_text("hru_id,layer,row,col,area_m2,fraction\n1,1,0,2,100,1\n")
    with pytest.raises(EngineInputError, match="1-based"):
        cio.read_hru_cells(p, (2, 6, 8))
    p.write_text("hru_id,layer,row,col,area_m2,fraction\n1,1,2,9,100,1\n")
    with pytest.raises(EngineInputError, match="col outside"):
        cio.read_hru_cells(p, (2, 6, 8))


def test_sfr_reach_numbering(tmp_path):
    fx.write_connections(tmp_path)
    r = cio.read_sfr_reaches(tmp_path / "sfr_reaches.csv", (2, 6, 8))
    assert r.n == 6 and list(r.rno) == list(range(6))
    by = r.reaches_by_channel()
    assert set(by) == {1, 2} and abs(sum(f for _, f in by[1]) - 1.0) < 1e-12
    (tmp_path / "gap.csv").write_text("rno,channel_id,seq,layer,row,col,length_m\n1,1,1,1,1,1,10\n3,1,2,1,2,1,10\n")
    with pytest.raises(EngineInputError, match="contiguous"):
        cio.read_sfr_reaches(tmp_path / "gap.csv")


# ------------------------------------------------------------------ forcing

def test_recharge_delay_conserves_volume_apart_from_in_transit():
    perc = np.zeros((400, 2))
    perc[10] = [50.0, 10.0]
    out = forcing.apply_recharge_delay(perc, 5.0)
    assert np.all(out >= 0)
    assert out.sum() == pytest.approx(perc.sum(), rel=1e-9, abs=1e-9)   # tail fully released within 400 days
    short = forcing.apply_recharge_delay(perc[:14], 5.0)
    assert short.sum() < perc.sum()                                      # the rest is still in transit
    assert forcing.apply_recharge_delay(perc, 0.0) is perc


def _schedule(update, n_days=75, start=date(2001, 1, 16)):
    import scipy.sparse as sp
    rng = np.random.default_rng(3)
    perc = rng.uniform(0, 8, size=(n_days, 3))
    W = sp.csr_matrix(np.array([[1.0, 0.0, 0.0], [0.5, 0.5, 0.0], [0.0, 0.0, 2.0], [0.0, 0.0, 0.0]]))
    dates = forcing.day_list(start, n_days)
    return perc, W, dates, forcing.RechargeSchedule(perc, dates, W, update)


@pytest.mark.parametrize("update", ["daily", "monthly", "monthly_ramped"])
def test_recharge_schedule_conserves_volume_per_month(update):
    perc, W, dates, sched = _schedule(update)
    rates = np.array([sched.rates(i) for i in range(len(dates))])
    assert rates.min() >= 0.0
    daily = (perc * 1e-3) @ W.toarray().T
    for key in {(d.year, d.month) for d in dates}:
        idx = [i for i, d in enumerate(dates) if (d.year, d.month) == key]
        assert rates[idx].sum(axis=0) == pytest.approx(daily[idx].sum(axis=0), rel=1e-9)
    assert rates[:, 3].max() == 0.0                                       # a column no HRU touches


def test_monthly_holds_constant_and_ramp_does_not_jump():
    _, _, dates, m = _schedule("monthly")
    first = [m.rates(i) for i in range(len(dates)) if dates[i].month == 2]
    assert all(np.array_equal(first[0], r) for r in first)
    _, _, dates, r = _schedule("monthly_ramped")
    rates = np.array([r.rates(i) for i in range(len(dates))])
    jump = np.abs(np.diff(rates[:, 0]))
    _, _, _, m2 = _schedule("monthly")
    step = np.abs(np.diff(np.array([m2.rates(i) for i in range(len(dates))])[:, 0]))
    assert jump.max() < step.max()


def test_ramped_month_volume_matches_exactly():
    prev, cur, nxt = np.array([0.0, 2.0]), np.array([4.0, 2.0]), np.array([1.0, 0.0])
    out = forcing.ramped_month_recharge(prev, cur, nxt, 31, 28, 31)
    assert out.sum(axis=0) == pytest.approx(cur * 28)


def test_weights_reject_two_layers_per_column(tmp_path):
    p = tmp_path / "h.csv"
    p.write_text("hru_id,layer,row,col,area_m2,fraction\n1,1,2,2,100,0.5\n2,2,2,2,100,0.5\n")
    hc = cio.read_hru_cells(p, (2, 6, 8))
    with pytest.raises(EngineInputError, match="one layer per column"):
        forcing.build_weights([1, 2], hc, {1: 100.0, 2: 100.0}, 10000.0, 6, 8)


def test_weights_use_hru_con_area_and_cell_area():
    import scipy.sparse as sp  # noqa: F401
    hc = cio.HruCells(np.array([1, 1]), np.array([0, 0]), np.array([0, 0]), np.array([0, 1]),
                      np.array([0.25, 0.75]), np.array([1.0, 3.0]))
    W, lay = forcing.build_weights([1], hc, {1: 40000.0}, 10000.0, 1, 2)
    # 1 mm over 40000 m2 = 40 m3; 25 % into cell 0 -> 10 m3 over 10000 m2 = 1e-3 m
    assert W.toarray().tolist() == [[1.0], [3.0]]
    assert list(lay) == [0, 0]


def test_runoff_is_local_flow_with_seep_added_back_and_reservoir_handled():
    d = 4
    f1, f2, f3 = np.full(d, 1.0), np.full(d, 2.0), np.full(d, 2.5)   # m3/s; channel 3 is below a reservoir
    flow = np.column_stack([f1, f2, f3])
    seep = np.column_stack([np.full(d, 100.0)] * 3)
    up = {-1: [1, 2], 3: [-1]}
    chan_reaches = {1: [(0, 0.5), (1, 0.5)], 2: [(2, 1.0)], 3: [(3, 1.0)]}
    res = forcing.build_runoff(flow, seep, [1, 2, 3], chan_reaches, up, 4)
    # local flows: ch1 = 1, ch2 = 2, ch3 = 2.5 - (1 + 2) = -0.5 m3/s; plus 100 m3/day seep each
    assert np.allclose(res.runoff_m3d[:, 0], (1.0 * 86400 + 100) * 0.5)
    assert np.allclose(res.runoff_m3d[:, 3], -0.5 * 86400 + 100)
    # nothing created or destroyed: the net equals the downstream-most flow plus the seep added back
    assert res.runoff_m3d.sum(axis=1) == pytest.approx(np.full(d, 2.5 * 86400 + 300))
    assert res.unmapped_m3 == 0.0


def test_runoff_reports_unmapped_channels():
    flow = np.column_stack([np.full(3, 1.0), np.full(3, 3.0)])
    res = forcing.build_runoff(flow, None, [1, 2], {1: [(0, 1.0)]}, {2: [1]}, 1)
    assert res.unmapped_m3 == pytest.approx(2.0 * 86400 * 3)         # channel 2's local flow is not injected
    assert res.runoff_m3d.sum() == pytest.approx(1.0 * 86400 * 3)


def test_lane_loss_bounds_and_water_moved_not_created():
    v = np.array([0.0, 50.0, 500.0, 5000.0, 50000.0])
    out = transmission.lane_outflow_lateral(v, 2.0, 5.0, 0.2, 12.0)
    assert np.all(out >= 0) and np.all(out <= v + 1e-9)
    assert out[0] == 0.0
    loss = v - out
    assert np.all(np.diff(loss) >= 0)                               # a larger event loses at least as much
    flow = np.column_stack([np.full(3, 0.05)])
    trans = dict(losing={1}, len_km={1: 2.0}, wd_m={1: 5.0}, k_in_per_h=0.2, duration_h=12.0)
    res = forcing.build_runoff(flow, None, [1], {1: [(0, 1.0)]}, {}, 1, trans=trans)
    base = flow[:, 0] * 86400
    assert res.runoff_m3d[:, 0] + res.loss_reach_m3d[:, 0] == pytest.approx(base)   # moved, not destroyed
    assert res.loss_reach_m3d.sum() > 0


# ------------------------------------------------------------------ budget parser

_BUDGET = """
  VOLUME BUDGET FOR ENTIRE MODEL AT END OF TIME STEP    1, STRESS PERIOD   7
  ---------------------------------------------------------------------------------------------------

     CUMULATIVE VOLUME      L**3       RATES FOR THIS TIME STEP      L**3/T          PACKAGE NAME
     ------------------                 ------------------------                     ----------------

           IN:                                      IN:
           ---                                      ---
              STO-SS =       6.7615E-02                STO-SS =       6.7615E-02     STORAGE
              STO-SY =          46.2264                STO-SY =          46.2264     STORAGE
                RCHA =          29.0000                  RCHA =          29.0000     RCHA_0
                 SFR =           8.3214                   SFR =           8.3214     SFR_0

            TOTAL IN =          83.6154              TOTAL IN =          83.6154

          OUT:                                     OUT:
          ----                                     ----
              STO-SS =       3.6292E-02                STO-SS =       3.6292E-02     STORAGE
              STO-SY =          25.2584                STO-SY =          25.2584     STORAGE
                 WEL =          50.0000                   WEL =          50.0000     WEL_0
                RCHA =           0.0000                   RCHA =           0.0000     RCHA_0

           TOTAL OUT =          75.2946             TOTAL OUT =          75.2946

            IN - OUT =       8.3208                  IN - OUT =       8.3208

 PERCENT DISCREPANCY =           5.25     PERCENT DISCREPANCY =           5.25
"""


def test_budget_parser_reads_rates_and_discrepancy():
    recs, used = budget.parse_budget_text(_BUDGET)
    assert len(recs) == 1 and used > 0
    r = recs[0]
    assert r.kper == 7 and r.percent_discrepancy == 5.25
    assert r.net("RCHA_0") == 29.0 and r.net("WEL_0") == -50.0
    assert r.keys_of_kind("RCHA") == ["RCHA_0"]
    assert r.storage_in() == pytest.approx(46.294015) and r.storage_out() == pytest.approx(25.294692)
    assert r.total_in == 83.6154


def test_budget_parser_waits_for_a_complete_block(tmp_path):
    f = tmp_path / "x.lst"
    cut = _BUDGET.index("TOTAL OUT")
    f.write_text(_BUDGET[:cut])
    rd = budget.ListingBudgetReader(f)
    assert rd.poll() == []                                           # half-written block is not parsed
    with open(f, "a") as fh:
        fh.write(_BUDGET[cut:])
    got = rd.poll()
    assert len(got) == 1 and got[0].total_out == 75.2946
    assert rd.poll() == []


# ------------------------------------------------------------------ water-table recharge rule

def test_water_table_rule_picks_highest_wet_cell_and_falls_back():
    # 2 layers x 4 columns; reduced node numbers are 1-based, 0 = inactive
    red = np.array([[1, 2, 3, 0], [4, 5, 6, 7]])
    heads = np.array([15.0, 5.0, 9.0, 0, 8.0, 8.0, 8.0])             # index = reduced node - 1
    bot = np.array([[10.0, 10.0, 10.0, 10.0], [0.0, 0.0, 0.0, 0.0]])
    sel = _water_table_nodes(red, heads, bot)
    # col 0: layer 1 is wet (15 > 10) -> node 1; col 1: layer 1 dry (5 < 10) -> layer 2 node 5;
    # col 2: layer 1 dry (9 < 10) -> node 6; col 3: layer 1 inactive -> layer 2 node 7
    assert list(sel) == [1, 5, 6, 7]
    heads_all_dry = np.array([1.0, 1.0, 1.0, -1e30, -1e30, -1e30, -1e30])
    # nothing wet: the lowest active cell gets the water, never "no cell"
    assert list(_water_table_nodes(red, heads_all_dry, bot)) == [4, 5, 6, 7]


# ------------------------------------------------------------------ MODFLOW preparation (text level)

def test_inspect_and_working_copy(tmp_path):
    fx.build_modflow(tmp_path / "m")
    info = mf6_prepare.inspect_simulation(tmp_path / "m")
    assert (info.nlay, info.nrow, info.ncol) == (2, 6, 8)
    assert info.length_units == "METERS" and info.time_units == "DAYS"
    assert {"rch", "sfr", "evt", "wel", "sto"} <= {p.ftype for p in info.packages}

    from swatplus_modflow.project import SolverSection
    work = mf6_prepare.make_working_copy(info, tmp_path / "w", 12, SolverSection(outer_maximum=77),
                                         well_rows=[(1, 2, 2, -5.0)], well_auto_flow_reduce=0.1)
    tdis = (work / "sim.tdis").read_text()
    assert "NPER 12" in tdis and tdis.count("1.0 1 1.0") == 12
    assert "OUTER_MAXIMUM 77" in (work / "sim.ims").read_text()
    nam = (work / "gwf.nam").read_text()
    assert "NEWTON  UNDER_RELAXATION" in nam or "NEWTON UNDER_RELAXATION" in nam
    assert "swatplus_wells.wel" in nam
    assert "AUTO_FLOW_REDUCE" in (work / "swatplus_wells.wel").read_text()
    sto = (work / "gwf.sto").read_text().lower()
    assert "steady-state" not in sto and "transient" in sto
    assert "PRINT BUDGET ALL" in (work / "gwf.oc").read_text()
    # the user's folder is untouched
    assert "NPER  1\n" in (tmp_path / "m" / "sim.tdis").read_text()
    with pytest.raises(EngineInputError, match="not empty"):
        mf6_prepare.make_working_copy(info, work, 12, SolverSection())


def test_model_with_later_stress_periods_is_refused(tmp_path):
    fx.build_modflow(tmp_path / "m", extra_period=True)
    # flopy writes period-2 blocks only if data differ; force one
    wel = tmp_path / "m" / "gwf.wel"
    wel.write_text(wel.read_text() + "\nBEGIN period 2\n  1 4 3 -10.0\nEND period 2\n")
    info = mf6_prepare.inspect_simulation(tmp_path / "m")
    assert any("period 2" in p for p in info.problems)


def test_units_are_asserted(tmp_path):
    from swatplus_modflow.engine.driver import check_units
    fx.build_modflow(tmp_path / "m", length_units="FEET")
    with pytest.raises(EngineInputError, match="LENGTH_UNITS"):
        check_units(mf6_prepare.inspect_simulation(tmp_path / "m"))
    fx.build_modflow(tmp_path / "ok")
    check_units(mf6_prepare.inspect_simulation(tmp_path / "ok"))


def test_non_structured_grid_is_refused(tmp_path):
    fx.build_modflow(tmp_path / "m")
    nam = tmp_path / "m" / "gwf.nam"
    nam.write_text(nam.read_text().replace("DIS6", "DISV6"))
    with pytest.raises(EngineInputError, match="structured"):
        mf6_prepare.inspect_simulation(tmp_path / "m")


# ------------------------------------------------------------------ facade (DEFECT-38) and CLI

def test_file_based_facade_fails_loudly_instead_of_solving_the_original_model(tmp_path):
    from swatplus_modflow.interfaces.modflow_interface import FileBasedCouplingNotSupported, MODFLOWInterface
    mi = MODFLOWInterface(tmp_path)
    with pytest.raises(FileBasedCouplingNotSupported, match="swatplus-modflow run"):
        mi.advance_one_day({0: 1.0}, {}, {}, {}, date(2001, 1, 1))


def test_cli_version_and_validate_errors(tmp_path, capsys):
    import subprocess
    from swatplus_modflow import __version__
    from swatplus_modflow.cli import main
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == f"swatplus-modflow {__version__}"
    import os
    root = str(Path(__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": root + os.pathsep + os.environ.get("PYTHONPATH", "")}
    proc = subprocess.run([sys.executable, "-m", "swatplus_modflow", "version"], capture_output=True, text=True,
                          cwd=root, env=env)
    assert proc.returncode == 0 and proc.stdout.strip() == f"swatplus-modflow {__version__}"
    assert main(["validate", str(tmp_path / "nope.yaml")]) == 1
    assert "ERROR: project file not found" in capsys.readouterr().out
    bad = tmp_path / "p.yaml"
    bad.write_text("schema_version: 1\nname: x\ncrs: 'EPSG:1'\n")
    assert main(["validate", str(bad)]) == 1
    out = capsys.readouterr().out
    assert out.count("ERROR:") >= 1 and "swatplus" in out
    assert main(["run", str(bad)]) == 1
    assert capsys.readouterr().out.splitlines()[-1].startswith("RESULT failed")
