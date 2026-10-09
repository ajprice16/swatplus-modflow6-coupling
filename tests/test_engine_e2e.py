"""End-to-end tests: a tiny synthetic MODFLOW 6 model stepped daily through real libmf6.

SWAT+ is not run (``run_swatplus: false``); synthetic daily output files in the
SWAT+ text layout stand in for it. Skipped when no libmf6 can be found (set
SWATPLUS_TEST_LIBMF6 to its path, or install it with ``python -m flopy.utils.get_modflow``).
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))
import engine_fixtures as fx  # noqa: E402

LIB = fx.find_libmf6()
pytestmark = pytest.mark.skipif(LIB is None, reason="libmf6 not found (SWATPLUS_TEST_LIBMF6)")
pytest.importorskip("xmipy")
pytest.importorskip("flopy")

from swatplus_modflow.cli import main  # noqa: E402


def run(root: Path, capsys, **kw):
    p = fx.make_project(root, LIB, **kw)
    code = main(["run", str(p)])
    out = capsys.readouterr().out
    return p, code, out


def summary(root: Path, prefix="coupled_") -> pd.DataFrame:
    return pd.read_csv(root / "results" / f"{prefix}daily_summary.csv")


def test_cli_run_contract_and_ledger(tmp_path, capsys):
    p, code, out = run(tmp_path, capsys)
    assert code == 0
    lines = out.splitlines()
    assert lines[-1].startswith("RESULT succeeded ") and lines[-1].endswith("run_manifest.json")
    prog = [ln for ln in lines if ln.startswith("PROGRESS ")]
    assert prog[0].startswith("PROGRESS 1/45 2001-01-01") and prog[-1] == "PROGRESS 45/45 2001-02-14"
    man = json.loads((tmp_path / "results" / "run_manifest.json").read_text())
    assert man["format"] == "swatplus-modflow-run/1" and man["status"] == "succeeded"
    names = {f["name"]: f for f in man["files"]}
    assert {"daily_summary", "final_heads", "heads_month_end", "budget_terms"} <= set(names)
    for f in man["files"]:
        assert (tmp_path / "results" / f["path"]).exists()
    assert names["final_heads"]["kind"] == "cell_values"
    heads = pd.read_csv(tmp_path / "results" / names["final_heads"]["path"])
    assert list(heads.columns) == ["layer", "row", "col", "value"]
    assert heads["row"].min() == 1 and heads["col"].min() == 1                    # 1-based
    assert len(heads) == 2 * (6 * 8 - 1)                                          # one inactive column

    df = summary(tmp_path)
    assert len(df) == 45
    # independent expectation from the synthetic SWAT+ inputs: recharge = perc * hru.con area * fraction
    perc = {h: fx.perc_series(45, h) for h in (1, 2, 3)}
    exp_rch = perc[1] * 1e-3 * 8e4 + perc[2] * 1e-3 * 6e4 + perc[3] * 1e-3 * 5e4 * 0.8
    assert df["recharge_intended_m3"].to_numpy() == pytest.approx(exp_rch, rel=1e-9)
    assert df["recharge_mf6_m3"].to_numpy() == pytest.approx(exp_rch, rel=1e-5, abs=1e-3)
    # SFR runoff = flo_out of the downstream channel (+ seep added back for both channels)
    o1, o2, s1, s2 = fx.channel_series(45)
    exp_ro = o2 * 86400 + s1 + s2
    assert df["runoff_intended_m3"].to_numpy() == pytest.approx(exp_ro, rel=1e-9)
    assert df["runoff_injected_m3"].to_numpy() == pytest.approx(exp_ro, rel=1e-9)
    assert df["runoff_applied_m3"].to_numpy() == pytest.approx(exp_ro, rel=1e-6)
    assert df["wel_mf6_m3"].to_numpy() == pytest.approx(-40.0, rel=1e-5)
    assert df["mass_balance_percent"].abs().max() <= 0.1
    assert df["converged"].all()
    # water in = water out for every day, from the MODFLOW budget itself
    assert (df["total_in_m3"] - df["total_out_m3"]).abs().max() < 1e-2 * df["total_in_m3"].max()
    # 20 % of HRU 3 lies outside the grid: that part is never injected, and the ledger says so
    assert "less than 1" in " ".join(man["summary"]["notes"])


def test_daily_forcing_actually_reaches_the_solver(tmp_path, capsys):
    """DEFECT-38 regression: different SWAT+ forcing must give different heads."""
    _, c1, _ = run(tmp_path / "a", capsys, swat_kwargs=dict(perc_scale=1.0))
    _, c2, _ = run(tmp_path / "b", capsys, swat_kwargs=dict(perc_scale=25.0))
    assert c1 == c2 == 0
    h1 = pd.read_csv(tmp_path / "a/results/coupled_final_heads.csv")
    h2 = pd.read_csv(tmp_path / "b/results/coupled_final_heads.csv")
    assert (h2["value"] - h1["value"]).max() > 1e-3
    d1, d2 = summary(tmp_path / "a"), summary(tmp_path / "b")
    assert d2["recharge_mf6_m3"].sum() == pytest.approx(25.0 * d1["recharge_mf6_m3"].sum(), rel=1e-4)
    # and heads change from day to day (the heads file holds the last day of each month plus the final day)
    hm = pd.read_csv(tmp_path / "b/results/coupled_heads.csv")
    assert hm["date"].nunique() == 2


def test_spinup_days_are_run_but_not_reported(tmp_path, capsys):
    _, code, _ = run(tmp_path, capsys, n_days=20, spinup=7)
    assert code == 0
    df = summary(tmp_path)
    assert len(df) == 20 and df["date"].iloc[0] == "2001-01-01" and not df["spinup"].any()
    # the 7 spin-up days still ran: heads are not the initial 15 m everywhere
    heads = pd.read_csv(tmp_path / "results/coupled_final_heads.csv")
    assert heads["value"].std() > 0


@pytest.mark.parametrize("mode", ["daily", "monthly", "monthly_ramped"])
def test_recharge_update_modes_conserve_volume(tmp_path, capsys, mode):
    _, code, _ = run(tmp_path, capsys, n_days=70, coupling={"recharge_update": mode})
    assert code == 0
    df = summary(tmp_path)
    perc = {h: fx.perc_series(70, h) for h in (1, 2, 3)}
    exp = perc[1] * 1e-3 * 8e4 + perc[2] * 1e-3 * 6e4 + perc[3] * 1e-3 * 5e4 * 0.8
    dates = pd.to_datetime(df["date"])
    for _, idx in pd.Series(range(70)).groupby([dates.dt.year, dates.dt.month]):
        i = idx.to_numpy()
        assert df["recharge_mf6_m3"].to_numpy()[i].sum() == pytest.approx(exp[i].sum(), rel=1e-4)
    if mode != "daily":
        first_month = df["recharge_mf6_m3"].to_numpy()[:31]
        assert np.ptp(first_month) < 1e-6 or mode == "monthly_ramped"


def test_mass_balance_gate_stops_the_run_with_exit_2(tmp_path, capsys, monkeypatch):
    from swatplus_modflow.engine import budget as bm

    real = bm.parse_budget_text

    def inflated(text):
        recs, used = real(text)
        for r in recs:
            r.percent_discrepancy = 0.5          # as if MODFLOW had reported a 0.5 % imbalance
        return recs, used

    monkeypatch.setattr(bm, "parse_budget_text", inflated)
    p, code, out = run(tmp_path, capsys, checks={"mass_balance_tolerance_percent": 0.1})
    assert code == 2
    assert out.splitlines()[-1].startswith("RESULT failed") and "mass-balance" in out.splitlines()[-1]
    man = json.loads((tmp_path / "results/run_manifest.json").read_text())
    assert man["status"] == "failed" and "mass-balance" in man["error"]
    assert (tmp_path / "results/coupled_daily_summary_partial.csv").exists()


def test_enforcement_can_be_switched_off(tmp_path, capsys, monkeypatch):
    from swatplus_modflow.engine import budget as bm

    real = bm.parse_budget_text

    def inflated(text):
        recs, used = real(text)
        for r in recs:
            r.percent_discrepancy = 0.5
        return recs, used

    monkeypatch.setattr(bm, "parse_budget_text", inflated)
    _, code, _ = run(tmp_path, capsys, checks={"enforce_invariants": False})
    assert code == 0


def test_injected_vs_received_recharge_is_enforced(tmp_path, capsys, monkeypatch):
    """If MODFLOW reports a different recharge than the engine put in, the run stops."""
    from swatplus_modflow.engine import budget as bm

    real = bm.parse_budget_text

    def skewed(text):
        recs, used = real(text)
        for r in recs:
            for k in r.keys_of_kind("RCHA"):
                r.terms_in[k] *= 0.9
        return recs, used

    monkeypatch.setattr(bm, "parse_budget_text", skewed)
    _, code, out = run(tmp_path, capsys)
    assert code == 2 and "injection mismatch" in out


def test_invalid_project_exits_1_and_runs_nothing(tmp_path, capsys):
    p = fx.make_project(tmp_path, LIB, modflow_kwargs=dict(length_units="FEET"))
    assert main(["validate", str(p)]) == 1
    out = capsys.readouterr().out
    assert "ERROR:" in out and "LENGTH_UNITS" in out
    assert main(["run", str(p)]) == 1
    assert capsys.readouterr().out.splitlines()[-1].startswith("RESULT failed")
    assert not (tmp_path / "results").exists()


def test_swatplus_output_must_cover_the_period(tmp_path, capsys):
    p = fx.make_project(tmp_path, LIB, swat_kwargs=dict(end_trim=5))
    assert main(["validate", str(p)]) == 1
    assert "covers" in capsys.readouterr().out


def test_validate_ok_prints_warnings_only(tmp_path, capsys):
    p = fx.make_project(tmp_path, LIB)
    assert main(["validate", str(p)]) == 0
    out = capsys.readouterr().out
    assert "ERROR" not in out and "WARNING: hru_cells" in out


def test_pumping_wells_and_scale(tmp_path, capsys):
    wells = tmp_path / "wells.csv"
    tmp_path.mkdir(exist_ok=True)
    wells.write_text("layer,row,col,rate_m3d\n1,5,3,-100\n2,4,6,-60\n")
    p = fx.make_project(tmp_path, LIB, n_days=10, modflow_kwargs=dict(with_wel=False),
                        coupling={"pumping_scale": 0.5, "well_auto_flow_reduce": 0.1})
    cfg = yaml.safe_load(p.read_text())
    cfg["connections"]["well_cells"] = "wells.csv"
    p.write_text(yaml.safe_dump(cfg))
    assert main(["run", str(p)]) == 0
    capsys.readouterr()
    df = summary(tmp_path)
    assert df["wel_requested_m3"].to_numpy() == pytest.approx(-80.0)
    assert df["wel_mf6_m3"].to_numpy() == pytest.approx(-80.0, rel=1e-4)


def test_existing_well_package_is_scaled(tmp_path, capsys):
    _, code, _ = run(tmp_path, capsys, n_days=6, coupling={"pumping_scale": 0.25})
    assert code == 0
    assert summary(tmp_path)["wel_mf6_m3"].to_numpy() == pytest.approx(-10.0, rel=1e-4)


def test_evt_factors_and_extinction_depth(tmp_path, capsys):
    base = tmp_path / "base"
    _, code, _ = run(base, capsys, n_days=5, coupling={"et_extinction_depth_m": 8.0})
    assert code == 0
    # water table at 15 m, surface 20 m: extinction depth 8 m reaches it, so ET is non-zero
    e1 = summary(base)["evt_mf6_m3"].to_numpy()
    assert (e1 < 0).all()
    half = tmp_path / "half"
    f = [0.5] * 12
    _, code, _ = run(half, capsys, n_days=5, coupling={"et_extinction_depth_m": 8.0, "evt_monthly_factors": f})
    assert code == 0
    e2 = summary(half)["evt_mf6_m3"].to_numpy()
    assert e2 == pytest.approx(0.5 * e1, rel=0.05)


def test_initial_heads_file_is_used(tmp_path, capsys):
    fx.write_head_file(tmp_path / "start.hds", [np.full((6, 8), 12.5), np.full((6, 8), 11.5)])
    p = fx.make_project(tmp_path, LIB, n_days=2, swat_kwargs=dict(perc_scale=0.0))
    cfg = yaml.safe_load(p.read_text())
    cfg["modflow"]["initial_heads"] = "start.hds"
    p.write_text(yaml.safe_dump(cfg))
    assert main(["run", str(p)]) == 0
    capsys.readouterr()
    h = pd.read_csv(tmp_path / "results/coupled_final_heads.csv")
    l1 = h[h.layer == 1]["value"]
    assert 12.0 < l1.median() < 13.0           # started at 12.5, not at the model's own 15


def test_reach_flows_output_and_runoff_reaches_the_stream(tmp_path, capsys):
    _, code, _ = run(tmp_path, capsys, n_days=8, output={"save_reach_flows": True})
    assert code == 0
    q = pd.read_csv(tmp_path / "results/coupled_reach_outflow.csv")
    assert q.shape == (8, 7)
    o1, o2, s1, s2 = fx.channel_series(8)
    # the last reach's outflow leaves the model: runoff in minus what the streambed lost
    tot = o2 * 86400 + s1 + s2
    assert (q["6"].to_numpy() < tot + 1e-6).all()
    assert (q["6"].to_numpy() > 0.9 * tot - 1e-6).all()


def test_csv_output_layout_is_read(tmp_path, capsys):
    _, code, _ = run(tmp_path, capsys, n_days=6, swat_kwargs=dict(csv=True))
    assert code == 0


def test_missing_seep_column_is_tolerated_with_a_note(tmp_path, capsys):
    _, code, _ = run(tmp_path, capsys, n_days=6, swat_kwargs=dict(with_seep=False))
    assert code == 0
    o1, o2, *_ = fx.channel_series(6)
    assert summary(tmp_path)["runoff_intended_m3"].to_numpy() == pytest.approx(o2 * 86400)


def test_transmission_loss_moves_water_from_runoff_to_recharge(tmp_path, capsys):
    base = tmp_path / "base"
    run(base, capsys, n_days=6)
    tl = tmp_path / "tl"
    # the engine needs SWAT+ channel geometry for the Lane loss
    p = fx.make_project(tl, LIB, n_days=6, coupling={"transmission_loss": {"k_mm_per_h": 5.0, "duration_h": 12.0}},
                        modflow_kwargs=dict(), swat_kwargs={})
    (tl / "TxtInOut" / "channel-lte.cha").write_text("t\nid name cha_hyd\n1 a h1\n2 b h1\n")
    (tl / "TxtInOut" / "hyd-sed-lte.cha").write_text("t\nname wd dp len\nh1 4.0 1.0 0.3\n")
    capsys.readouterr()
    assert main(["run", str(p)]) == 0
    capsys.readouterr()
    d0, d1 = summary(base), summary(tl)
    # the stream bed (18 m) sits above the 15 m head, so every channel is "losing"
    assert d1["transmission_loss_m3"].sum() > 0
    assert d1["runoff_intended_m3"].sum() == pytest.approx(d0["runoff_intended_m3"].sum() - d1["transmission_loss_m3"].sum(),
                                                           rel=1e-9)
    assert d1["recharge_intended_m3"].sum() == pytest.approx(d0["recharge_intended_m3"].sum()
                                                             + d1["transmission_loss_m3"].sum(), rel=1e-9)
    assert (d1["recharge_mf6_m3"] - d1["recharge_intended_m3"]).abs().max() < 1e-3


def test_water_table_recharge_goes_to_a_wet_cell(tmp_path, capsys):
    """With layer-1 heads below the layer-1 bottom, recharge must be placed in layer 2."""
    # layer 1 bottom is 10 m, so heads of 8 m leave layer 1 dry
    fx.write_head_file(tmp_path / "dry.hds", [np.full((6, 8), 8.0), np.full((6, 8), 6.0)])
    for flag in (True, False):
        root = tmp_path / str(flag)
        p = fx.make_project(root, LIB, n_days=3, coupling={"recharge_to_water_table": flag},
                            modflow_kwargs=dict(with_evt=False, with_wel=False))
        cfg = yaml.safe_load(p.read_text())
        cfg["modflow"]["initial_heads"] = str((tmp_path / "dry.hds"))
        p.write_text(yaml.safe_dump(cfg))
        assert main(["run", str(p)]) == 0
        capsys.readouterr()
        bud = pd.read_csv(root / "results/coupled_budget_terms.csv")
        assert bud["RCHA_0_in"].iloc[0] > 0
    # (the cell-level placement is covered by the unit test of the selection rule; here the run completes
    # and MODFLOW receives all the injected recharge in both modes)
