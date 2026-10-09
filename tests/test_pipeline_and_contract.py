"""build-connections -> build-modflow -> validate -> run, plus the contract clarifications."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))
import build_fixtures as bf  # noqa: E402
import engine_fixtures as fx  # noqa: E402

from swatplus_modflow.builders.grid import read_grid  # noqa: E402
from swatplus_modflow.builders.modflow_builder import build_modflow  # noqa: E402
from swatplus_modflow.builders.sfr import build_sfr  # noqa: E402
from swatplus_modflow.builders.spec import load_spec  # noqa: E402
from swatplus_modflow.cli import main  # noqa: E402

LIB = fx.find_libmf6()
if LIB is None and fx.LIB_REQUIRED:      # CI sets this so a missing library fails instead of skipping
    raise RuntimeError("SWATPLUS_REQUIRE_LIBMF6 is set but no libmf6 was found")


def _pipeline(root: Path, capsys):
    bf.write_layers(root)
    assert main(["build-connections", "--grid", str(root / "grid.json"), "--hrus", str(root / "hrus.gpkg"),
                 "--channels", str(root / "channels.gpkg"), "--out-dir", str(root / "conn")]) == 0
    fx.write_swatplus(root / "TxtInOut", date(2001, 1, 1), 20)
    spec = bf.spec_dict()
    spec["sfr"] = dict(channel_cells="conn/channel_cells.csv", topology={"chandeg_con": "TxtInOut/chandeg.con"},
                       incision_m=1.0, bed_thickness_m=1.0, bed_k_m_per_day=1.0, mannings_n=0.035,
                       width_m=3.0, min_slope=1.0e-4)
    spec["evt"] = dict(rate_m_per_day=1.0e-4, extinction_depth_m=3.0)
    assert main(["build-modflow", str(bf.write_spec(root, spec))]) == 0
    capsys.readouterr()
    cfg = {
        "schema_version": 1, "name": "pipeline", "crs": bf.CRS,
        "swatplus": {"txtinout": "TxtInOut", "run_swatplus": False},
        "modflow": {"sim_dir": "model", "libmf6": str(LIB) if LIB else "missing"},
        "connections": {"hru_cells": "conn/hru_cells.csv", "channel_reaches": "model/sfr_reaches.csv",
                        "channel_cells": "conn/channel_cells.csv", "grid": "model/grid.json"},
        "period": {"start": "2001-01-01", "end": "2001-01-20"},
        "coupling": {"recharge_update": "daily"},
    }
    p = root / "project.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return p


@pytest.mark.skipif(LIB is None, reason="libmf6 not found (SWATPLUS_TEST_LIBMF6)")
def test_full_pipeline_from_gis_layers_to_a_daily_run(tmp_path, capsys):
    p = _pipeline(tmp_path, capsys)
    assert main(["validate", str(p)]) == 0
    out = capsys.readouterr().out
    assert "ERROR" not in out
    assert main(["run", str(p)]) == 0
    text = capsys.readouterr().out.splitlines()
    result = text[-1]
    assert result.startswith("RESULT succeeded ") and Path(result.split(" ", 2)[2]).is_absolute()
    df = pd.read_csv(tmp_path / "results/coupled_daily_summary.csv")
    assert len(df) == 20 and df["converged"].all() and df["mass_balance_percent"].abs().max() <= 0.1
    assert (df["recharge_mf6_m3"] - df["recharge_intended_m3"]).abs().max() < 1e-3
    assert df["runoff_injected_m3"].to_numpy() == pytest.approx(df["runoff_intended_m3"].to_numpy())
    assert df["recharge_intended_m3"].sum() > 0 and df["runoff_intended_m3"].sum() > 0
    # every synthetic channel lies inside the grid, so nothing is reassigned from off-grid length
    assert "runoff_from_offgrid_length_m3" in df and (df["runoff_from_offgrid_length_m3"] == 0).all()


def test_validate_says_to_build_first_when_sfr_reaches_is_missing(tmp_path, capsys):
    p = _pipeline_no_model(tmp_path)
    assert main(["validate", str(p)]) == 1
    out = capsys.readouterr().out
    assert "connections.channel_reaches" in out and "build-modflow" in out and "run the build first" in out


def _pipeline_no_model(root: Path) -> Path:
    bf.write_layers(root)
    fx.build_modflow(root / "model")
    fx.write_connections(root / "conn")
    (root / "conn" / "sfr_reaches.csv").unlink() if (root / "conn" / "sfr_reaches.csv").exists() else None
    fx.write_swatplus(root / "TxtInOut", date(2001, 1, 1), 5)
    cfg = {"schema_version": 1, "name": "x", "crs": bf.CRS, "swatplus": {"txtinout": "TxtInOut", "run_swatplus": False},
           "modflow": {"sim_dir": "model", "libmf6": sys.executable},
           "connections": {"hru_cells": "conn/hru_cells.csv", "channel_reaches": "model/sfr_reaches.csv"},
           "period": {"start": "2001-01-01", "end": "2001-01-05"}}
    p = root / "project.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


def test_validate_checks_grid_json_against_the_model(tmp_path, capsys):
    root = tmp_path
    fx.build_modflow(root / "model")
    fx.write_connections(root / "conn")
    fx.write_swatplus(root / "TxtInOut", date(2001, 1, 1), 5)
    bf.grid_json(root / "grid.json")                         # 10 x 12, the model is 6 x 8
    cfg = {"schema_version": 1, "name": "x", "crs": bf.CRS, "swatplus": {"txtinout": "TxtInOut", "run_swatplus": False},
           "modflow": {"sim_dir": "model", "libmf6": sys.executable},
           "connections": {"hru_cells": "conn/hru_cells.csv", "channel_reaches": "conn/sfr_reaches.csv",
                           "grid": "grid.json"},
           "period": {"start": "2001-01-01", "end": "2001-01-05"}}
    p = root / "project.yaml"
    p.write_text(yaml.safe_dump(cfg))
    assert main(["validate", str(p)]) == 1
    assert "grid.json is 10 x 12" in capsys.readouterr().out


def test_project_schema_additions_are_optional():
    from swatplus_modflow.project import json_schema
    props = json_schema()["$defs"]
    conn = props["ConnectionsSection"]
    assert {"channel_cells", "grid"} <= set(conn["properties"])
    assert set(conn["required"]) == {"hru_cells", "channel_reaches"}


# ---------------------------------------------------------------- contract clarifications

def test_repeated_channel_cell_pieces_are_accepted():
    from swatplus_modflow.builders.grid import Grid
    g = Grid(bf.CRS, bf.X0, bf.Y0, bf.CELL, bf.CELL, bf.NROW, bf.NCOL)
    # the channel leaves cell (2,3) and re-enters it: same (channel_id, row, col), different seq
    cells = pd.DataFrame({"channel_id": [1, 1, 1], "seq": [1, 2, 3], "layer": 1, "row": [2, 3, 2],
                          "col": [3, 3, 3], "length_m": [30.0, 40.0, 20.0], "fraction": [0.4, 0.4, 0.2]})
    top = np.full((g.nrow, g.ncol), 50.0)
    botm = np.stack([top - 20.0, top - 60.0])
    sb = build_sfr(cells, {1: 0}, g, top, botm, np.ones((2, g.nrow, g.ncol), int), incision_m=1.0,
                   bed_thickness_m=1.0, bed_k=1.0, mannings_n=0.03, width_m=3.0, widths=None, min_slope=1e-4,
                   clearance_m=0.01)
    assert len(sb.reaches) == 3 and list(sb.reaches.seq) == [1, 2, 3]
    assert list(sb.reaches[["row", "col"]].itertuples(index=False, name=None)) == [(2, 3), (3, 3), (2, 3)]


def test_channel_fractions_below_one_warn_and_are_not_rescaled():
    from swatplus_modflow.builders.grid import Grid
    g = Grid(bf.CRS, bf.X0, bf.Y0, bf.CELL, bf.CELL, bf.NROW, bf.NCOL)
    cells = pd.DataFrame({"channel_id": [1, 1], "seq": [1, 2], "layer": 1, "row": [2, 3], "col": [3, 3],
                          "length_m": [50.0, 50.0], "fraction": [0.3, 0.3]})
    top = np.full((g.nrow, g.ncol), 50.0)
    botm = np.stack([top - 20.0, top - 60.0])
    sb = build_sfr(cells, {1: 0}, g, top, botm, np.ones((2, g.nrow, g.ncol), int), incision_m=1.0,
                   bed_thickness_m=1.0, bed_k=1.0, mannings_n=0.03, width_m=3.0, widths=None, min_slope=1e-4,
                   clearance_m=0.01)
    assert any("outside the grid" in n and "not rescaled" in n for n in sb.notes)
    assert cells["fraction"].tolist() == [0.3, 0.3]                      # input untouched


def test_domain_cells_that_only_touch_the_boundary_are_inactive(tmp_path):
    import geopandas as gpd
    bf.write_layers(tmp_path)
    # polygon covering columns 0..4 exactly: column 5 only touches its east edge
    gpd.GeoDataFrame({"id": [1]}, geometry=[bf.cell_box(0, bf.NROW - 1, 0, 4)], crs=bf.CRS).to_file(
        tmp_path / "domain.gpkg", driver="GPKG")
    res = build_modflow(load_spec(bf.write_spec(tmp_path, bf.spec_dict())))
    assert res.report["active_cells_per_layer"] == [bf.NROW * 5] * 2
    layer = gpd.read_file(tmp_path / "model" / "grid_cells.gpkg", layer="grid")
    assert len(layer) == bf.NROW * bf.NCOL and set(layer.columns) >= {"row", "col", "active"}
    assert int(layer["active"].sum()) == bf.NROW * 5 and layer["row"].min() == 1 and layer["col"].min() == 1
    # a polygon that overlaps column 5 by a sliver does activate it
    gpd.GeoDataFrame({"id": [1]}, geometry=[bf.cell_box(0, bf.NROW - 1, 0, 4).union(
        bf.cell_box(0, bf.NROW - 1, 5, 5).buffer(-bf.CELL * 0.49))], crs=bf.CRS).to_file(
        tmp_path / "domain.gpkg", driver="GPKG")
    res = build_modflow(load_spec(bf.write_spec(tmp_path, bf.spec_dict())))
    assert res.report["active_cells_per_layer"][0] > bf.NROW * 5


def test_build_connections_can_use_the_grid_layer_active_flag(tmp_path, capsys):
    bf.write_layers(tmp_path)
    build_modflow(load_spec(bf.write_spec(tmp_path, bf.spec_dict())))
    out = tmp_path / "c"
    assert main(["build-connections", "--grid", str(tmp_path / "grid.json"), "--grid-layer",
                 str(tmp_path / "model/grid_cells.gpkg"), "--grid-layer-name", "grid", "--hrus",
                 str(tmp_path / "hrus.gpkg"), "--out-dir", str(out)]) == 0
    h = pd.read_csv(out / "hru_cells.csv")
    assert h["col"].max() <= bf.NCOL - 1                            # the inactive last column receives no rows


def test_csv_files_carry_no_comment_or_version_line(tmp_path):
    bf.write_layers(tmp_path)
    main(["build-connections", "--grid", str(tmp_path / "grid.json"), "--hrus", str(tmp_path / "hrus.gpkg"),
          "--channels", str(tmp_path / "channels.gpkg"), "--out-dir", str(tmp_path / "o")])
    for f in ("hru_cells.csv", "channel_cells.csv"):
        first = (tmp_path / "o" / f).read_text().splitlines()[0]
        assert first[0].isalpha() and not first.startswith("#") and "format" not in first


def test_committed_project_schema_matches_the_models():
    import json
    from swatplus_modflow.project import json_schema
    committed = json.loads((Path(__file__).resolve().parents[1] / "docs" / "project_schema.json").read_text())
    assert committed == json_schema(), "docs/project_schema.json is stale: regenerate it from json_schema()"


def test_build_example_loads_against_its_schema_shape(tmp_path):
    """build.example.yaml parses (paths are placeholders; only the structure is validated)."""
    from swatplus_modflow.builders.spec import BuildSpec
    data = yaml.safe_load((Path(__file__).resolve().parents[1] / "build.example.yaml").read_text())
    spec = BuildSpec.model_validate(data, context={"base_dir": tmp_path})
    assert spec.build_version == 1 and spec.properties.zones and spec.sfr is not None
    priorities = [z.priority for z in spec.properties.zones]
    assert len(set(priorities)) == len(priorities)


def test_connection_fractions_keep_full_precision(tmp_path):
    bf.write_layers(tmp_path)
    assert main(["build-connections", "--grid", str(tmp_path / "grid.json"), "--hrus", str(tmp_path / "hrus.gpkg"),
                 "--channels", str(tmp_path / "channels.gpkg"), "--out-dir", str(tmp_path / "conn")]) == 0
    hru = pd.read_csv(tmp_path / "conn/hru_cells.csv")
    sums = hru.groupby("hru_id")["fraction"].sum()
    inside = sums[sums > 0.99]                      # HRUs entirely inside the grid
    assert len(inside) >= 2
    assert np.abs(inside - 1.0).max() < 1e-12      # 6-decimal rounding lost ~1e-5 per HRU


@pytest.mark.skipif(LIB is None, reason="libmf6 not found (SWATPLUS_TEST_LIBMF6)")
def test_end_of_run_budget_failure_fails_the_run(tmp_path, capsys, monkeypatch):
    from swatplus_modflow.utils import water_budget
    seen = {}

    def fake_validate(self, tolerance_percent=1.0):
        seen["tol"] = tolerance_percent
        return False
    monkeypatch.setattr(water_budget.WaterBudgetTracker, "validate_mass_balance", fake_validate)
    p = _pipeline(tmp_path, capsys)
    assert main(["run", str(p)]) == 2
    assert seen["tol"] == pytest.approx(0.1)        # project tolerance, not loosened to 1%
    manifest = __import__("json").loads((tmp_path / "results/run_manifest.json").read_text())
    assert manifest["status"] == "failed"
