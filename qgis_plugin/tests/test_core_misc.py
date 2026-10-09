import json
import math
from datetime import date

import pytest

from swatplus_modflow_qgis.core import channel_order as co
from swatplus_modflow_qgis.core import contract, engine, manifest, project_file, validation
from swatplus_modflow_qgis.core.grid import GridSpec

GRID = GridSpec("EPSG:32633", 0.0, 0.0, 100.0, 100.0, 4, 5)


# ---- channel ordering ----------------------------------------------------
def test_chain_parts_joins_and_flips():
    a = [(0, 0), (10, 0)]
    b = [(20, 0), (10, 0)]        # digitised against the flow
    c = [(20, 0), (30, 0)]
    chain, gaps = co.chain_parts([c, a, b])
    assert gaps == 0
    assert chain[0] == a and chain[1] == [(10, 0), (20, 0)] and chain[2] == c


def test_chain_parts_counts_gaps():
    chain, gaps = co.chain_parts([[(0, 0), (1, 0)], [(5, 0), (6, 0)]], tol=0.01)
    assert gaps == 1 and len(chain) == 2


def test_orient_reverse_and_downhill():
    parts = [[(0, 0), (1, 0)], [(1, 0), (2, 0)]]
    assert co.orient(parts, True) == [[(2, 0), (1, 0)], [(1, 0), (0, 0)]]
    assert co.end_points(parts) == ((0, 0), (2, 0))
    assert co.decide_downhill(10.0, 5.0) is True
    assert co.decide_downhill(5.0, 10.0) is False
    assert co.decide_downhill(5.0, 5.0) is None and co.decide_downhill(None, 1.0) is None


# ---- contract tables -------------------------------------------------------
def test_contract_roundtrip(tmp_path):
    rows = [{"hru_id": 1, "layer": 1, "row": 2, "col": 3, "area_m2": 6000.0, "fraction": 0.6},
            {"hru_id": 1, "layer": 1, "row": 2, "col": 4, "area_m2": 4000.0, "fraction": 0.4}]
    p = tmp_path / "hru_cells.csv"
    assert contract.write_hru_cells(p, rows) == 2
    assert p.read_text().splitlines()[0] == "hru_id,layer,row,col,area_m2,fraction"
    assert contract.read_hru_cells(p) == rows
    crow = [{"channel_id": 7, "seq": 1, "layer": 1, "row": 1, "col": 1, "length_m": 10.0, "fraction": 1.0}]
    q = tmp_path / "channel_cells.csv"
    contract.write_channel_cells(q, crow)
    assert q.read_text().splitlines()[0] == "channel_id,seq,layer,row,col,length_m,fraction"
    assert contract.read_channel_cells(q) == crow
    (tmp_path / "bad.csv").write_text("hru_id,row\n1,2\n")
    with pytest.raises(contract.ContractError):
        contract.read_hru_cells(tmp_path / "bad.csv")


# ---- validation ------------------------------------------------------------
def test_check_hru_table_flags_partial_duplicates_and_outside():
    rows = [{"hru_id": 1, "layer": 1, "row": 1, "col": 1, "area_m2": 50.0, "fraction": 0.5},
            {"hru_id": 1, "layer": 1, "row": 1, "col": 1, "area_m2": 50.0, "fraction": 0.5},
            {"hru_id": 2, "layer": 1, "row": 9, "col": 1, "area_m2": 100.0, "fraction": 1.0}]
    rep = validation.check_hru_table(rows, GRID, {1: 100.0, 2: 100.0, 3: 10.0})
    codes = {i.code for i in rep.issues}
    assert {"hru-duplicate", "hru-outside-grid", "hru-uncovered"} <= codes
    ok = validation.check_hru_table(
        [{"hru_id": 1, "layer": 1, "row": 1, "col": 1, "area_m2": 40.0, "fraction": 0.4},
         {"hru_id": 1, "layer": 1, "row": 1, "col": 2, "area_m2": 60.0, "fraction": 0.6}], GRID, {1: 100.0})
    assert ok.ok and not ok.warnings
    partial = validation.check_hru_table(
        [{"hru_id": 1, "layer": 1, "row": 1, "col": 1, "area_m2": 40.0, "fraction": 0.4}], GRID, {1: 100.0})
    assert partial.ok and [i.code for i in partial.warnings] == ["hru-partial"]


def test_check_channel_table_seq_and_sums():
    good = [{"channel_id": 1, "seq": 1, "layer": 1, "row": 1, "col": 1, "length_m": 30.0, "fraction": 0.3},
            {"channel_id": 1, "seq": 2, "layer": 1, "row": 1, "col": 2, "length_m": 70.0, "fraction": 0.7}]
    assert validation.check_channel_table(good, GRID, {1: 100.0}).ok
    bad = [dict(good[0], seq=2), dict(good[1], seq=2)]
    assert "chan-seq" in {i.code for i in validation.check_channel_table(bad, GRID).issues}
    assert "html" in validation.check_channel_table(bad, GRID).to_html().lower()


# ---- project file ----------------------------------------------------------
def _project(tmp_path, **kw):
    args = dict(name="demo", crs="EPSG:32633", txtinout="swat/TxtInOut", sim_dir="mf6", libmf6="lib/libmf6.so",
                hru_cells="conn/hru_cells.csv", channel_reaches="conn/sfr_reaches.csv",
                start=date(2000, 1, 1), end=date(2000, 12, 31), executable="/path/to/swatplus")
    args.update(kw)
    return project_file.build_project_dict(base_dir=str(tmp_path), **args)


def test_project_dict_checks_and_json(tmp_path):
    d = _project(tmp_path, spinup_days=30, recharge_update="daily", pumping_scale=0.0,
                 transmission_loss_k_mm_per_h=2.0, output_directory=str(tmp_path / "out"))
    assert project_file.check_project_dict(d) == []
    assert d["output"]["directory"] == "out" and d["period"]["start"] == "2000-01-01"
    assert d["coupling"]["transmission_loss"] == {"k_mm_per_h": 2.0}
    p = tmp_path / "project.json"
    project_file.write_project_json(d, p)
    assert project_file.read_project_json(p) == d


def test_project_dict_problems(tmp_path):
    d = _project(tmp_path, executable=None)
    assert any("executable" in m for m in project_file.check_project_dict(d))
    d = _project(tmp_path, crs="32633", end=date(1999, 1, 1))
    msgs = " ".join(project_file.check_project_dict(d))
    assert "crs" in msgs and "after" in msgs


def test_relativize(tmp_path):
    assert project_file.relativize(str(tmp_path / "a" / "b.csv"), str(tmp_path)) == "a/b.csv"
    outside = str(tmp_path.parent / "elsewhere" / "x.csv")
    assert project_file.relativize(outside, str(tmp_path)) == outside
    assert project_file.relativize("rel/x.csv", str(tmp_path)) == "rel/x.csv"


def test_engine_parsing_and_commands():
    assert engine.parse_progress("PROGRESS 3/10 2000-01-03") == engine.ProgressLine(3, 10, "2000-01-03")
    assert engine.parse_progress("PROGRESS 11/10 x") is None and engine.parse_progress("hello") is None
    assert engine.parse_result("RESULT succeeded /x/run_manifest.json") == engine.ResultLine("succeeded", "/x/run_manifest.json")
    assert engine.parse_result("RESULT failed solver did not converge").detail == "solver did not converge"
    assert engine.parse_findings("ERROR: a\nWARNING: b\nnoise\nERROR: c") == (["a", "c"], ["b"])
    assert engine.run_args("p.json") == ["-m", "swatplus_modflow", "run", "p.json"]
    cmds = engine.venv_commands("/py/python3", "/v", "/src/pkg", windows=False)
    assert cmds[0] == ("/py/python3", ["-m", "venv", "/v"])
    assert cmds[-1] == ("/v/bin/python", ["-m", "pip", "install", "/src/pkg"])
    assert engine.venv_commands("py", "C:v", "pkg", windows=True)[1][0].endswith("python.exe")
    with pytest.raises(ValueError):
        engine.venv_commands("py", "v", "--index-url=x")


def test_manifest_reading(tmp_path):
    (tmp_path / "t.csv").write_text("a\n1\n")
    (tmp_path / "h.csv").write_text("layer,row,col,value,date\n1,1,1,5.0,2000-01-01\n1,1,1,6.0,2000-01-02\n1,2,1,7.0,2000-01-02\n")
    (tmp_path / "run_manifest.json").write_text(json.dumps({
        "format": "swatplus-modflow-run/1", "project": "p", "status": "succeeded", "started": "s", "finished": "f",
        "files": [{"kind": "table", "name": "t", "path": "t.csv"},
                  {"kind": "cell_values", "name": "h", "path": "h.csv", "units": "m"}]}))
    m = manifest.read_manifest(tmp_path / "run_manifest.json")
    assert [f.kind for f in m.files] == ["table", "cell_values"] and m.files[1].units == "m"
    rows, has_date = manifest.read_cell_values(m.files[1].path)
    assert has_date
    by_layer, latest = manifest.latest_by_layer(rows)
    assert latest == "2000-01-02" and by_layer[1] == {(1, 1): 6.0, (2, 1): 7.0}
    (tmp_path / "bad.json").write_text(json.dumps({"format": "swatplus-modflow-run/2", "files": []}))
    with pytest.raises(manifest.ManifestError):
        manifest.read_manifest(tmp_path / "bad.json")
