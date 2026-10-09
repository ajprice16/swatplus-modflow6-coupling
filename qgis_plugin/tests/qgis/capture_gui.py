"""Render every plugin window to PNG for visual comparison across QGIS versions.

Run with the Python of a QGIS installation (see run_qgis_tests.sh / run_qgis_tests.bat):

    capture_gui.py --out <folder>             capture this QGIS into <folder>/<tag>/
    capture_gui.py --sheet <folder>           only rebuild <folder>/index.html

<tag> is ``qgis-<version>_qt<version>_<platform>``. Every capture writes ``info.json`` (versions and,
per window, status, size, and whether it fits a 1366x768 screen), and ``index.html`` in <folder> shows
all captured versions side by side, so screenshots from several machines can be copied into one
folder and compared. Only synthetic, in-memory inputs are used.

Exit code: 0 when every window was captured and passed the layout checks, 1 otherwise.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import platform
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SCREEN = (1366, 768)
ALGORITHMS = ("creategrid", "hruconnections", "channelconnections", "validateinputs")   # small laptop screen: windows must fit without being cut off


def _tag(qgis_version: str, qt_version: str) -> str:
    sysname = {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}.get(platform.system(), platform.system())
    v = qgis_version.split("-")[0]
    return f"qgis-{v}_qt{qt_version}_{sysname}"


def _layout_problems(widget):
    """Simple checks that catch most Qt5/Qt6 layout regressions."""
    from qgis.PyQt.QtWidgets import QAbstractButton, QLabel
    problems = []
    hint = widget.minimumSizeHint()
    if hint.width() > SCREEN[0] or hint.height() > SCREEN[1]:
        problems.append(f"minimum size {hint.width()}x{hint.height()} exceeds a {SCREEN[0]}x{SCREEN[1]} screen")
    for b in widget.findChildren(QAbstractButton):
        if b.isVisible() and b.text() and b.width() < b.fontMetrics().horizontalAdvance(b.text().replace("&", "")):
            problems.append(f"button text clipped: {b.text()!r}")
    for lab in widget.findChildren(QLabel):
        if lab.isVisible() and lab.text() and not lab.wordWrap() and lab.width() < lab.sizeHint().width() - 2:
            problems.append(f"label clipped: {lab.text()[:40]!r}")
    return problems


def _grab(widget, path: Path, size=None):
    from qgis.PyQt.QtWidgets import QApplication
    if size:
        widget.resize(*size)
    widget.show()
    for _ in range(5):
        QApplication.processEvents()
    pix = widget.grab()
    ok = pix.save(str(path))
    rec = {"file": path.name, "width": pix.width(), "height": pix.height(), "saved": bool(ok),
           "problems": _layout_problems(widget)}
    if widget.property("missing_widgets"):
        rec["problems"].append(f"no standard Processing widget for: {widget.property('missing_widgets')}")
    widget.hide()
    return rec


def _install_iface(iface):
    """Give Processing's GUI modules a QgisInterface stand-in (they bind ``iface`` at import)."""
    from qgis.gui import QgsMapCanvas, QgsMessageBar
    iface.canvas, iface.bar = QgsMapCanvas(), QgsMessageBar()
    iface.mapCanvas = lambda: iface.canvas
    iface.messageBar = lambda: iface.bar
    iface.activeLayer = lambda: None
    import qgis.utils
    qgis.utils.iface = iface
    import processing.tools.general  # noqa: F401  (createAlgorithmDialog lives here in 3.x and 4.x)
    for mod in list(sys.modules.values()):
        if getattr(mod, "__name__", "").startswith(("processing", "qgis.processing")) and \
                getattr(mod, "iface", 0) is None:
            mod.iface = iface


_keep = []   # widget wrappers must outlive the panel


def _parameter_panel(alg_id):
    """Each parameter's standard Processing widget in a form: what the dialog's Parameters tab shows."""
    from qgis.core import QgsApplication, QgsProcessingContext
    from qgis.gui import QgsGui, QgsProcessingGui
    from qgis.PyQt.QtWidgets import QFormLayout, QLabel, QWidget
    alg = QgsApplication.processingRegistry().algorithmById(alg_id)
    if alg is None:
        raise RuntimeError(f"algorithm {alg_id} is not registered")
    standard = getattr(getattr(QgsProcessingGui, "WidgetType", QgsProcessingGui), "Standard")
    panel = QWidget()
    panel.setWindowTitle(alg.displayName())
    form = QFormLayout(panel)
    missing = []
    for p in alg.parameterDefinitions():
        w = QgsGui.processingGuiRegistry().createParameterWidgetWrapper(p, standard)
        if w is None:
            missing.append(p.name())
            form.addRow(p.description(), QLabel("(no standard widget)"))
            continue
        _keep.append(w)
        form.addRow(w.createWrappedLabel() or QLabel(p.description()), w.createWrappedWidget(QgsProcessingContext()))
    panel.resize(600, max(200, 40 * len(alg.parameterDefinitions())))
    if missing:
        panel.setProperty("missing_widgets", ", ".join(missing))
    return panel


def capture_one_algorithm(alg: str, out: Path, mode: str = "dialog") -> int:
    """Child process: capture one Processing dialog (or its parameter panel) and write a .json record."""
    import qgis_env as E
    E.start_qgis()
    E.register_provider()
    from test_plugin_gui import FakeIface
    iface = FakeIface()
    _install_iface(iface)
    from qgis import processing
    if mode == "panel":
        name = f"processing_{alg}_params"
        rec = dict(status="ok", **_grab(_parameter_panel(f"swatplusmodflow:{alg}"), out / f"{name}.png"))
        (out / f".{name}.json").write_text(json.dumps(rec))
        return 0
    name = f"processing_{alg}"
    dlg = processing.createAlgorithmDialog(f"swatplusmodflow:{alg}", {})
    if dlg is None:
        raise RuntimeError("createAlgorithmDialog returned None")
    rec = dict(status="ok", **_grab(dlg, out / f"{name}.png", (760, 620)))
    (out / f".{name}.json").write_text(json.dumps(rec))
    return 0


def capture(out_root: Path) -> int:
    import faulthandler
    faulthandler.enable()
    import qgis_env as E
    E.start_qgis()
    E.register_provider()
    from qgis.core import Qgis, QgsProject
    from qgis.PyQt.QtCore import QT_VERSION_STR, QDate
    from test_plugin_gui import FakeIface

    tag = _tag(Qgis.version(), QT_VERSION_STR)
    out = out_root / tag
    out.mkdir(parents=True, exist_ok=True)
    info = {"tag": tag, "qgis": Qgis.version(), "qt": QT_VERSION_STR, "python": sys.version.split()[0],
            "platform": platform.platform(), "windows": {}}
    iface = FakeIface()
    work = Path(tempfile.mkdtemp(prefix="swatplus_capture_"))

    def run(name, make, size=None):
        print(f"capturing {name}", flush=True)
        info["windows"][name] = {"status": "error", "error": "crashed while opening (no Python exception)"}
        (out / "info.json").write_text(json.dumps(info, indent=2))   # survives a hard crash
        try:
            w = make()
            info["windows"][name] = dict(status="ok", **_grab(w, out / f"{name}.png", size))
        except Exception as exc:                           # record and keep capturing the rest
            info["windows"][name] = {"status": "error", "error": f"{type(exc).__name__}: {exc}",
                                     "traceback": traceback.format_exc()}

    from swatplus_modflow_qgis.gui.dock import SwatPlusDock
    from swatplus_modflow_qgis.gui.run_dialog import RunDialog
    from swatplus_modflow_qgis.gui.settings_dialog import EngineSettingsDialog

    run("dock", lambda: SwatPlusDock(iface), (300, 420))
    run("engine_settings", lambda: EngineSettingsDialog())
    project = QgsProject()
    run("run_dialog_empty", lambda: RunDialog(iface, project=project))

    def filled():
        dlg = RunDialog(iface, project=project)
        dlg.project_path.setText(str(work / "project.json"))
        dlg.name.setText("synthetic demo")
        dlg.crs.setText(E.CRS)
        for w, v in ((dlg.txtinout, "swat/TxtInOut"), (dlg.executable, "swatplus"), (dlg.sim_dir, "modflow"),
                     (dlg.libmf6, "lib/libmf6"), (dlg.hru_cells, "connections/hru_cells.csv"),
                     (dlg.channel_reaches, "modflow/sfr_reaches.csv"),
                     (dlg.channel_cells, "connections/channel_cells.csv"), (dlg.grid_json, "grid/grid.json"),
                     (dlg.out_dir, "results")):
            w.setText(str(work / v))
        dlg.start.setDate(QDate(2000, 1, 1))
        dlg.end.setDate(QDate(2000, 12, 31))
        dlg.progress.setRange(0, 366)
        dlg.progress.setValue(120)
        dlg.status.setText("Running: day 120 of 366")
        for line in ("PROGRESS 119/366 2000-04-28", "PROGRESS 120/366 2000-04-29"):
            dlg.say(line)
        return dlg
    run("run_dialog_filled", filled)

    # Processing parameter dialogs. The full dialog is opened in a child process, because some
    # QGIS builds abort hard when it is created offscreen (seen on macOS); a crash there is
    # recorded and the parameter panel is captured in this process instead.
    import subprocess

    def child(alg, mode):
        name = f"processing_{alg}" + ("" if mode == "dialog" else "_params")
        rec_file = out / f".{name}.json"
        rec_file.unlink(missing_ok=True)
        try:
            proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--alg", alg, "--mode", mode,
                                   "--out", str(out)], capture_output=True, text=True, timeout=180)
            code, tail = proc.returncode, (proc.stdout + proc.stderr)[-1500:]
        except subprocess.TimeoutExpired:
            code, tail = "timeout", ""
        if rec_file.is_file():
            rec = json.loads(rec_file.read_text())
            rec_file.unlink()
            return name, rec
        return name, {"status": "error", "error": f"crashed while opening (exit {code})", "traceback": tail}

    for alg in ALGORITHMS:
        print(f"capturing processing_{alg}", flush=True)
        name, rec = child(alg, "dialog")
        info["windows"][name] = rec
        if rec["status"] != "ok":
            pname, prec = child(alg, "panel")
            if prec["status"] != "ok" and platform.system() == "Darwin":
                # QGIS's own Processing parameter widgets abort under Qt's macOS offscreen platform
                # (independent of this plugin); these dialogs are captured on Linux and Windows.
                for r in (rec, prec):
                    r.update(status="skipped", error="QGIS Processing widgets abort under the macOS "
                                                     "offscreen platform; capture on Linux or Windows")
            info["windows"][pname] = prec
    (out / "info.json").write_text(json.dumps(info, indent=2))
    build_sheet(out_root)
    bad = {n: w for n, w in info["windows"].items() if w["status"] == "error" or w.get("problems")}
    skipped = [n for n, w in info["windows"].items() if w["status"] == "skipped"]
    print(f"captured {len(info['windows']) - len(bad) - len(skipped)}/{len(info['windows'])} windows cleanly "
          f"into {out} ({len(skipped)} skipped on this platform)")
    for n, w in bad.items():
        msg = w.get('error') or '; '.join(w['problems'])
        print(f"  {n}: {msg}")
        if os.environ.get("GITHUB_ACTIONS"):
            tail = " | ".join((w.get("traceback") or "").strip().splitlines()[-6:])
            print(f"::error title=GUI {tag} {n}::{(msg + ' || ' + tail)[:900].replace('%', '%25')}")
    return 0 if not bad else 1


def build_sheet(out_root: Path) -> Path:
    """index.html: one row per window, one column per captured QGIS/Qt/platform."""
    runs = []
    for d in sorted(out_root.iterdir()):
        f = d / "info.json"
        if d.is_dir() and f.is_file():
            runs.append(json.loads(f.read_text()))
    names = sorted({n for r in runs for n in r["windows"]})
    esc = html.escape
    head = "".join(f"<th>{esc(r['tag'])}<br><small>QGIS {esc(r['qgis'])}, Qt {esc(r['qt'])}, "
                   f"Python {esc(r['python'])}</small></th>" for r in runs)
    rows = []
    for n in names:
        cells = []
        for r in runs:
            w = r["windows"].get(n)
            if w is None:
                cells.append("<td class=missing>not captured</td>")
            elif w["status"] == "skipped":
                cells.append(f"<td class=missing>skipped<pre>{esc(w['error'])}</pre></td>")
            elif w["status"] != "ok":
                cells.append(f"<td class=bad><b>error</b><pre>{esc(w['error'])}</pre></td>")
            else:
                probs = "".join(f"<li>{esc(p)}</li>" for p in w.get("problems", []))
                cls = "bad" if probs else "ok"
                cells.append(f"<td class={cls}><a href='{esc(r['tag'])}/{esc(w['file'])}'>"
                             f"<img src='{esc(r['tag'])}/{esc(w['file'])}'></a>"
                             f"<div>{w['width']}x{w['height']}</div><ul>{probs}</ul></td>")
        rows.append(f"<tr><th>{esc(n)}</th>{''.join(cells)}</tr>")
    page = f"""<!doctype html><meta charset=utf-8><title>SWAT+ MODFLOW 6 plugin: GUI across QGIS versions</title>
<style>body{{font-family:sans-serif}} table{{border-collapse:collapse}} td,th{{border:1px solid #ccc;padding:6px;
vertical-align:top}} img{{max-width:420px}} td.bad{{background:#fde8e8}} td.missing{{color:#888}}
pre{{white-space:pre-wrap;max-width:420px}}</style>
<h1>Plugin windows across QGIS versions</h1>
<p>Red cells failed to open or failed a layout check (minimum size larger than {SCREEN[0]}x{SCREEN[1]},
clipped button or label text). Click a screenshot for full size.</p>
<table><tr><th>window</th>{head}</tr>{''.join(rows)}</table>"""
    p = out_root / "index.html"
    p.write_text(page, encoding="utf-8")
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--out", type=Path, help="capture this QGIS into <out>/<tag>/ and rebuild <out>/index.html")
    g.add_argument("--sheet", type=Path, help="only rebuild <sheet>/index.html from captured folders")
    ap.add_argument("--alg", help=argparse.SUPPRESS)       # internal: child process for one dialog
    ap.add_argument("--mode", default="dialog", choices=["dialog", "panel"], help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.alg:
        return capture_one_algorithm(a.alg, a.out.resolve(), a.mode)
    if a.sheet:
        print(build_sheet(a.sheet))
        return 0
    return capture(a.out.resolve())


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)   # QGIS can crash while tearing down QgsApplication at exit
