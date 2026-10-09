"""Tests for the versioned project configuration (synthetic paths only)."""
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from swatplus_modflow.project import SCHEMA_VERSION, json_schema, load_project, save_project

REPO = Path(__file__).resolve().parents[1]


def _minimal(**over):
    d = {
        "schema_version": 1,
        "name": "toy",
        "crs": "epsg:32632",
        "swatplus": {"txtinout": "swat/TxtInOut", "executable": "swatplus"},
        "modflow": {"sim_dir": "mf6", "libmf6": "lib/libmf6.so"},
        "connections": {"hru_cells": "conn/hru.csv", "channel_reaches": "conn/riv.csv"},
        "period": {"start": "2001-01-01", "end": "2001-12-31"},
    }
    for k, v in over.items():
        d[k] = v
    return d


def _write(tmp_path, data, name="project.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data))
    return p


def test_relative_paths_resolve_against_project_folder(tmp_path):
    cfg = load_project(_write(tmp_path, _minimal()))
    assert cfg.swatplus.txtinout == (tmp_path / "swat/TxtInOut").resolve()
    assert cfg.modflow.libmf6 == (tmp_path / "lib/libmf6.so").resolve()
    assert cfg.output.directory == (tmp_path / "results").resolve()
    assert cfg.crs == "EPSG:32632"


def test_defaults_are_neutral(tmp_path):
    cfg = load_project(_write(tmp_path, _minimal()))
    assert cfg.coupling.mode == "sequential_daily"
    assert cfg.coupling.recharge_delay_days == 0.0
    assert cfg.coupling.pumping_scale == 1.0
    assert cfg.checks.enforce_invariants is True


def test_json_written_by_stdlib_is_accepted(tmp_path):
    p = tmp_path / "project.yaml"
    p.write_text(json.dumps(_minimal()))
    assert load_project(p).name == "toy"


def test_unknown_key_rejected(tmp_path):
    d = _minimal()
    d["coupling"] = {"recharge_delay_dys": 5}
    with pytest.raises(ValidationError, match="recharge_delay_dys"):
        load_project(_write(tmp_path, d))


def test_wrong_schema_version_rejected(tmp_path):
    with pytest.raises(ValueError, match="schema_version"):
        load_project(_write(tmp_path, _minimal(schema_version=SCHEMA_VERSION + 1)))


@pytest.mark.parametrize("over, msg", [
    ({"crs": "UTM zone 1"}, "AUTHORITY:CODE"),
    ({"period": {"start": "2001-01-01", "end": "2000-01-01"}}, "must be after"),
    ({"swatplus": {"txtinout": "x"}}, "executable is required"),
])
def test_invalid_values_rejected(tmp_path, over, msg):
    with pytest.raises(ValidationError, match=msg):
        load_project(_write(tmp_path, _minimal(**over)))


def test_missing_paths_reported(tmp_path):
    cfg = load_project(_write(tmp_path, _minimal()))
    missing = cfg.missing_paths()
    assert any(m.startswith("modflow.libmf6") for m in missing)
    (tmp_path / "mf6").mkdir()
    assert not any(m.startswith("modflow.sim_dir") for m in load_project(_write(tmp_path, _minimal())).missing_paths())


def test_round_trip(tmp_path):
    cfg = load_project(_write(tmp_path, _minimal()))
    out = tmp_path / "copy" / "project.yaml"
    out.parent.mkdir()
    save_project(cfg, out)
    assert load_project(out) == cfg


def test_example_file_matches_schema(tmp_path):
    text = (REPO / "project.example.yaml").read_text().replace("EPSG:<code>", "EPSG:32632")
    cfg = load_project(_write(tmp_path, yaml.safe_load(text)))
    assert cfg.period.spinup_days == 365


def test_json_schema_exports():
    s = json_schema()
    assert s["properties"]["schema_version"]["const"] == 1
    assert set(s["required"]) >= {"schema_version", "name", "crs", "swatplus", "modflow", "connections", "period"}
