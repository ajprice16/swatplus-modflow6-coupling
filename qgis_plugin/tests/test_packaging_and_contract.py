"""Plain-pytest checks: zip layout, project file vs the engine's own loader, fake engine vs the CLI contract,
and a static scan for Qt5/Qt6 incompatibilities and forbidden imports."""
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PLUGIN_DIR = HERE.parent / "swatplus_modflow_qgis"
REPO = HERE.parents[1]
FAKE = HERE / "fake_engine"

sys.path.insert(0, str(HERE.parent))
import build_plugin_zip as bz  # noqa: E402

from swatplus_modflow_qgis.core import contract, engine as eng, project_file as pf  # noqa: E402


# ---- packaging -------------------------------------------------------------
def test_zip_layout(tmp_path):
    target = bz.build(tmp_path)
    assert target.name == "swatplus_modflow_qgis-0.1.0.zip"
    names = zipfile.ZipFile(target).namelist()
    assert names and all(n.startswith("swatplus_modflow_qgis/") for n in names)
    assert {"swatplus_modflow_qgis/metadata.txt", "swatplus_modflow_qgis/__init__.py", "swatplus_modflow_qgis/icon.svg",
            "swatplus_modflow_qgis/LICENSE", "swatplus_modflow_qgis/processing/create_grid.py",
            "swatplus_modflow_qgis/gui/run_dialog.py", "swatplus_modflow_qgis/core/grid.py"} <= set(names)
    assert not [n for n in names if "/tests/" in n or "__pycache__" in n or n.endswith((".pyc", ".DS_Store"))]
    assert bz.build(tmp_path / "again").read_bytes() == target.read_bytes()   # reproducible


def test_metadata_fields():
    meta = bz.read_metadata(PLUGIN_DIR / "metadata.txt")
    assert meta["qgisMinimumVersion"] == "3.40" and meta["qgisMaximumVersion"] == "4.99"
    assert meta["supportsQt6"] == "True"
    assert meta["hasProcessingProvider"] == "yes"
    assert (PLUGIN_DIR / meta["icon"]).exists()
    assert (PLUGIN_DIR / "LICENSE").read_text() == (REPO / "LICENSE").read_text()


# ---- project file against the engine's loader ------------------------------------
def _write_inputs(tmp):
    for d in ("swat", "mf", "conn"):
        (tmp / d).mkdir()
    (tmp / "lib.so").write_text("")
    (tmp / "conn" / "hru_cells.csv").write_text("hru_id,layer,row,col,area_m2,fraction\n")
    (tmp / "conn" / "sfr_reaches.csv").write_text("rno,channel_id,seq,layer,row,col,length_m\n")


def test_plugin_project_json_loads_with_engine_loader(tmp_path):
    load_project = pytest.importorskip("swatplus_modflow.project").load_project
    _write_inputs(tmp_path)
    data = pf.build_project_dict(
        name="synthetic", crs="EPSG:32633", txtinout=str(tmp_path / "swat"), sim_dir=str(tmp_path / "mf"),
        libmf6=str(tmp_path / "lib.so"), hru_cells=str(tmp_path / "conn" / "hru_cells.csv"),
        channel_reaches=str(tmp_path / "conn" / "sfr_reaches.csv"), start=date(2000, 1, 1), end=date(2000, 12, 31),
        executable="/path/to/swatplus", spinup_days=30, recharge_update="monthly_ramped", recharge_delay_days=2.5,
        pumping_scale=0.0, transmission_loss_k_mm_per_h=1.5, transmission_loss_duration_h=8.0,
        mass_balance_tolerance_percent=0.5, enforce_invariants=True, output_directory=str(tmp_path / "res"),
        output_prefix="run_", model_name="gwf", well_cells=None, base_dir=str(tmp_path))
    path = tmp_path / "project.json"
    pf.write_project_json(data, path)
    cfg = load_project(path)
    assert pf.check_project_dict(data) == []
    assert cfg.crs == "EPSG:32633" and cfg.period.spinup_days == 30
    assert cfg.coupling.recharge_update == "monthly_ramped" and cfg.coupling.transmission_loss.duration_h == 8.0
    assert cfg.connections.hru_cells == (tmp_path / "conn" / "hru_cells.csv").resolve()   # relative path resolved
    assert cfg.output.directory == (tmp_path / "res").resolve()
    assert cfg.missing_paths() == []
    assert json.loads(path.read_text())["schema_version"] == 1


def test_minimal_project_uses_engine_defaults(tmp_path):
    load_project = pytest.importorskip("swatplus_modflow.project").load_project
    _write_inputs(tmp_path)
    data = pf.build_project_dict(
        name="synthetic", crs="EPSG:32633", txtinout="swat", sim_dir="mf", libmf6="lib.so", hru_cells="conn/hru_cells.csv",
        channel_reaches="conn/sfr_reaches.csv", start="2000-01-01", end="2000-01-31", executable="x", base_dir=str(tmp_path))
    assert set(data) == {"schema_version", "name", "crs", "swatplus", "modflow", "connections", "period"}
    pf.write_project_json(data, tmp_path / "p.json")
    cfg = load_project(tmp_path / "p.json")
    assert cfg.coupling.recharge_update == "monthly" and cfg.checks.enforce_invariants is True
    bad = dict(data, crs="32633")
    pf.write_project_json(bad, tmp_path / "bad.json")
    with pytest.raises(Exception):
        load_project(tmp_path / "bad.json")


def test_connection_tables_have_contract_headers(tmp_path):
    hru = tmp_path / "h.csv"
    contract.write_hru_cells(hru, [{"hru_id": 1, "layer": 1, "row": 1, "col": 1, "area_m2": 2.0, "fraction": 1.0}])
    assert hru.read_text().splitlines()[0] == "hru_id,layer,row,col,area_m2,fraction"
    ch = tmp_path / "c.csv"
    contract.write_channel_cells(ch, [{"channel_id": 1, "seq": 1, "layer": 1, "row": 1, "col": 1, "length_m": 2.0, "fraction": 1.0}])
    assert ch.read_text().splitlines()[0] == "channel_id,seq,layer,row,col,length_m,fraction"


# ---- the fake engine obeys the CLI contract ----------------------------------------
def _engine(*args, cwd=None):
    env = dict(os.environ, PYTHONPATH=str(FAKE), FAKE_ENGINE_DELAY="0")
    return subprocess.run([sys.executable, "-m", "swatplus_modflow", *args], capture_output=True, text=True, cwd=cwd, env=env, timeout=60)


def test_fake_engine_cli_contract(tmp_path):
    _write_inputs(tmp_path)
    (tmp_path / "conn" / "hru_cells.csv").write_text(
        "hru_id,layer,row,col,area_m2,fraction\n1,1,1,1,1.0,0.5\n1,1,2,3,1.0,0.5\n")
    mk = lambda name: pf.write_project_json(pf.build_project_dict(
        name=name, crs="EPSG:32633", txtinout="swat", sim_dir="mf", libmf6="lib.so", hru_cells="conn/hru_cells.csv",
        channel_reaches="conn/sfr_reaches.csv", start="2000-01-01", end="2000-01-31", executable="x",
        output_directory="out", base_dir=str(tmp_path)), tmp_path / f"{name.replace(' ', '_')}.json")
    mk("good")
    mk("an invalid one")
    mk("failrun")
    r = _engine("version")
    assert r.returncode == 0 and "fake" in r.stdout
    r = _engine("validate", str(tmp_path / "good.json"))
    assert r.returncode == 0 and eng.parse_findings(r.stdout)[0] == []
    r = _engine("validate", str(tmp_path / "an_invalid_one.json"))
    assert r.returncode == eng.EXIT_INVALID and len(eng.parse_findings(r.stdout)[0]) == 1
    r = _engine("run", str(tmp_path / "failrun.json"))
    assert r.returncode == eng.EXIT_RUN_FAILED and r.stdout.strip().splitlines()[-1].startswith("RESULT failed")
    r = _engine("run", str(tmp_path / "good.json"))
    lines = r.stdout.strip().splitlines()
    assert r.returncode == 0
    prog = [eng.parse_progress(l) for l in lines if l.startswith("PROGRESS")]
    assert [p.done for p in prog] == [1, 2, 3, 4, 5] and all(p.total == 5 for p in prog)
    res = eng.parse_result(lines[-1])
    assert res.status == "succeeded"
    from swatplus_modflow_qgis.core import manifest
    man = manifest.read_manifest(res.detail)
    assert [f.kind for f in man.files] == ["table", "cell_values"] and all(os.path.exists(f.path) for f in man.files)
    rows, has_date = manifest.read_cell_values(man.files[1].path)
    assert has_date and {(r["row"], r["col"]) for r in rows} == {(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)}


# ---- static Qt5/Qt6 and dependency scan ------------------------------------------------
PLUGIN_SOURCES = sorted(PLUGIN_DIR.rglob("*.py"))

QT_BAD = [
    (r"\.exec_\(", "exec_() was removed in Qt6; use exec()"),
    (r"\bPyQt[56]\b", "import Qt through qgis.PyQt only"),
    (r"\bPySide[26]\b", "import Qt through qgis.PyQt only"),
    (r"\bQVariant\.(Int|Double|String|Bool|LongLong|Date|DateTime|List|Map|UInt)\b", "use QMetaType.Type for QgsField types"),
    (r"\bQMetaType\.(Int|Double|QString|Bool|LongLong|QDate|QDateTime)\b", "use scoped QMetaType.Type.<name>"),
    (r"\bQt\.(Align(?:Left|Right|Top|Bottom|Center|HCenter|VCenter|Justify|Leading|Trailing|Absolute)|Key_\w+|Checked|Unchecked|UserRole|DisplayRole|Horizontal|Vertical|Left|Right|Top|Bottom|"
     r"LeftDockWidgetArea|RightDockWidgetArea|TopDockWidgetArea|BottomDockWidgetArea|ItemIsEnabled|ItemIsSelectable|"
     r"WaitCursor|ArrowCursor|PointingHandCursor|ScrollBarAlwaysOff|NoPen|SolidLine|WindowModal|ApplicationModal|"
     r"ElideRight|RichText|PlainText|LeftButton|RightButton|ShiftModifier|ControlModifier)\b", "use fully scoped Qt enums"),
    (r"\bQMessageBox\.(Ok|Cancel|Yes|No|Save|Close|Discard|Abort|Retry|Ignore|Warning|Information|Critical|Question)\b",
     "use QMessageBox.StandardButton / .Icon"),
    (r"\bQDialogButtonBox\.(Ok|Cancel|Save|Close|Apply|Help|Reset)\b", "use QDialogButtonBox.StandardButton"),
    (r"\bQProcess\.(NotRunning|Starting|Running|FailedToStart|Crashed|Timedout|MergedChannels|SeparateChannels|NormalExit|CrashExit)\b",
     "use QProcess.ProcessState / ProcessError / ProcessChannelMode / ExitStatus"),
    (r"\bQFileDialog\.(ShowDirsOnly|DontUseNativeDialog|DontConfirmOverwrite|ExistingFile|Directory|AcceptOpen|AcceptSave)\b",
     "use scoped QFileDialog enums"),
    (r"\bQDialog\.(Accepted|Rejected)\b", "use QDialog.DialogCode"),
    (r"\bQSizePolicy\.(Expanding|Fixed|Minimum|Maximum|Preferred|MinimumExpanding|Ignored)\b", "use QSizePolicy.Policy"),
    (r"\bQFrame\.(HLine|VLine|Sunken|Raised|Plain|StyledPanel|NoFrame|Box|Panel)\b", "use QFrame.Shape / .Shadow"),
    (r"\bQgsWkbTypes\.(Point|LineString|Polygon|MultiPolygon|NoGeometry)\b", "use Qgis.WkbType"),
    (r"\bQgsProcessing\.Type\w+", "use Qgis.ProcessingSourceType"),
    (r"\bQgsProcessingParameterNumber\.(Integer|Double)\b", "use Qgis.ProcessingNumberParameterType"),
    (r"\bQgsProcessingParameterField\.(Any|Numeric|String|DateTime)\b", "use Qgis.ProcessingFieldParameterDataType"),
    (r"\bQgsMapLayerProxyModel\.\w+Layer\b", "use Qgis.LayerFilter"),
    (r"\bQgsVectorFileWriter\.NoError\b", "use WriterError.NoError (the plugin compares through int)"),
    (r"\bQgsFeatureSink\.(FastInsert|RegeneratePrimaryKey)\b", "pass no sink flags"),
    (r"\bQgsUnitTypes\b", "removed in QGIS 4; use Qgis.DistanceUnit"),
    (r"\bQDesktopWidget\b|\bQRegExp\b|\bQRegExpValidator\b", "removed in Qt6"),
    (r"\bQAction\b.*QtWidgets\b(?!.*ImportError)", "QAction moved to QtGui in Qt6"),
]
FORBIDDEN_IMPORTS = re.compile(r"^\s*(?:import|from)\s+(flopy|geopandas|pydantic|yaml|numpy|pandas|shapely|rasterio|scipy|"
                               r"swatplus_modflow\b(?!_qgis))", re.M)


@pytest.mark.parametrize("pattern, why", QT_BAD)
def test_no_qt5_qt6_incompatibility(pattern, why):
    rx = re.compile(pattern)
    hits = []
    for src in PLUGIN_SOURCES:
        for n, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if rx.search(code):
                hits.append(f"{src.relative_to(PLUGIN_DIR)}:{n}: {line.strip()}")
    assert not hits, why + "\n" + "\n".join(hits)


def test_plugin_imports_only_qgis_and_stdlib():
    hits = []
    for src in PLUGIN_SOURCES:
        text = src.read_text(encoding="utf-8")
        for m in FORBIDDEN_IMPORTS.finditer(text):
            hits.append(f"{src.relative_to(PLUGIN_DIR)}: {m.group(0).strip()}")
    assert not hits, "\n".join(hits)


def test_core_package_does_not_import_qgis():
    for src in (PLUGIN_DIR / "core").glob("*.py"):
        assert not re.search(r"^\s*(?:import|from)\s+(qgis|PyQt)", src.read_text(), re.M), src.name


def test_qaction_import_has_qt5_fallback():
    text = (PLUGIN_DIR / "plugin.py").read_text()
    assert "from qgis.PyQt.QtGui import QAction" in text and "except ImportError" in text


@pytest.mark.parametrize("snippet", [
    "dlg.exec_()", "from PyQt5.QtCore import Qt", "QgsField('a', QVariant.Int)", "x = Qt.AlignLeft",
    "QMessageBox.Ok", "QProcess.NotRunning", "QgsProcessingParameterNumber.Double", "QgsWkbTypes.Polygon",
])
def test_static_scan_detects_samples(snippet):
    assert any(re.search(p, snippet) for p, _ in QT_BAD), snippet


@pytest.mark.parametrize("snippet", [
    "dlg.exec()", "Qt.AlignmentFlag.AlignLeft", "QgsField('a', QMetaType.Type.Int)", "QMessageBox.StandardButton.Ok",
    "QProcess.ProcessState.NotRunning", "from qgis.PyQt.QtCore import Qt",
])
def test_static_scan_accepts_scoped_forms(snippet):
    assert not any(re.search(p, snippet) for p, _ in QT_BAD), snippet
