"""Build or load the project file, run the engine as a subprocess, load the results."""
from __future__ import annotations

import os
from typing import Optional

from qgis.core import Qgis, QgsProject
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import QDate
from qgis.PyQt.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                                 QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                                 QSpinBox, QVBoxLayout, QWidget)

from ..core import engine as eng
from ..core import project_file
from . import results, settings
from .engine_process import EngineProcess
from .widgets import PathEdit

ISO = "yyyy-MM-dd"


def _initial_size(widget, width, height):
    """Preferred size, shrunk to fit the screen (the form scrolls, so the dialog can be small)."""
    from qgis.PyQt.QtWidgets import QApplication
    screen = widget.screen() if hasattr(widget, "screen") else None
    screen = screen or QApplication.primaryScreen()
    if screen is None:
        return width, height
    avail = screen.availableGeometry()
    return min(width, int(avail.width() * 0.95)), min(height, int(avail.height() * 0.9))


class RunDialog(QDialog):
    """Project form + run controls.

    The form writes a project file as JSON (valid YAML). An existing YAML project can be run unchanged by
    leaving 'Use the form' unticked; the plugin does not parse YAML.
    """

    def __init__(self, iface, parent=None, project: Optional[QgsProject] = None):
        super().__init__(parent)
        self.iface = iface
        self.qgis_project = project or QgsProject.instance()
        self.setWindowTitle("SWAT+ MODFLOW 6 - project and run")
        self.resize(*_initial_size(self, 780, 820))
        self.proc: Optional[EngineProcess] = None
        self.last_manifest: Optional[str] = None
        self.loaded_layers = []

        root = QVBoxLayout(self)
        top = QFormLayout()
        self.project_path = PathEdit("save", "Project files (*.json *.yaml *.yml);;All files (*)", "Project file")
        top.addRow("Project file", self.project_path)
        self.use_form = QCheckBox("Use the form below (writes the project file as JSON before every run)")
        self.use_form.setChecked(True)
        top.addRow(self.use_form)
        self.engine_label = QLabel()
        top.addRow("Engine interpreter", self.engine_label)
        root.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        self.form = QFormLayout(body)
        self._build_form()
        root.addWidget(scroll, 3)

        grid_row = QFormLayout()
        self.grid_combo = QgsMapLayerComboBox()
        self.grid_combo.setFilters(Qgis.LayerFilters(Qgis.LayerFilter.PolygonLayer))
        if hasattr(self.grid_combo, "setProject"):
            self.grid_combo.setProject(self.qgis_project)
        self.grid_combo.setAllowEmptyLayer(True)
        grid_row.addRow("Grid layer for cell results", self.grid_combo)
        root.addLayout(grid_row)

        row = QHBoxLayout()
        self.load_button = QPushButton("Load project file")
        self.save_button = QPushButton("Write project file")
        self.validate_button = QPushButton("Validate with engine")
        self.run_button = QPushButton("Run")
        self.cancel_button = QPushButton("Cancel run")
        self.cancel_button.setEnabled(False)
        self.results_button = QPushButton("Load results...")
        for b in (self.load_button, self.save_button, self.validate_button, self.run_button, self.cancel_button, self.results_button):
            row.addWidget(b)
        root.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status = QLabel("Idle")
        root.addWidget(self.progress)
        root.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        root.addWidget(self.log, 2)

        self.load_button.clicked.connect(self.load_project)
        self.save_button.clicked.connect(self.write_project)
        self.validate_button.clicked.connect(self.validate)
        self.run_button.clicked.connect(self.run)
        self.cancel_button.clicked.connect(self.cancel)
        self.results_button.clicked.connect(self.load_results_dialog)
        self.refresh_engine_label()

    # -- form -------------------------------------------------------------------
    def _build_form(self):
        f = self.form
        self.name = QLineEdit()
        self.crs = QLineEdit(self.qgis_project.crs().authid() if self.qgis_project.crs().isValid() else "")
        self.crs.setPlaceholderText("AUTHORITY:CODE of the shared projected CRS")
        self.txtinout = PathEdit("folder", title="SWAT+ TxtInOut folder")
        self.executable = PathEdit("file", title="SWAT+ executable")
        self.run_swatplus = QCheckBox("Run SWAT+ first (otherwise reuse its daily outputs)")
        self.run_swatplus.setChecked(True)
        self.sim_dir = PathEdit("folder", title="MODFLOW 6 simulation folder")
        self.libmf6 = PathEdit("file", title="libmf6 shared library")
        self.model_name = QLineEdit()
        self.initial_heads = PathEdit("file", "MODFLOW head files (*.hds *.bhd);;All files (*)",
                                      "starting heads (optional)")
        self.initial_heads.setToolTip("Binary head file whose last record is used as starting heads. "
                                      "Default: the MODFLOW IC package")
        self.hru_cells = PathEdit("file", "CSV (*.csv)", "hru_cells.csv")
        self.channel_reaches = PathEdit("file", "CSV (*.csv)", "sfr_reaches.csv (written by build-modflow)")
        self.channel_reaches.setToolTip("sfr_reaches.csv written by 'swatplus-modflow build-modflow' "
                                        "into the MODFLOW folder")
        self.channel_cells = PathEdit("file", "CSV (*.csv)", "channel_cells.csv (optional)")
        self.channel_cells.setToolTip("channel_cells.csv from 'Build channel connections'. Optional; when set the "
                                      "engine reports the flow from channel length outside the grid that it "
                                      "injects into the in-grid reaches")
        self.grid_json = PathEdit("file", "JSON (*.json)", "grid.json (optional)")
        self.grid_json.setToolTip("grid.json from 'Create MODFLOW grid'. Optional; validate checks it against the "
                                  "MODFLOW model")
        self.well_cells = PathEdit("file", "CSV (*.csv)", "well cells table (optional)")
        today = QDate.currentDate()
        self.start = QDateEdit(today.addYears(-1))
        self.end = QDateEdit(today)
        for d in (self.start, self.end):
            d.setDisplayFormat(ISO)
            d.setCalendarPopup(True)
        self.spinup = QSpinBox()
        self.spinup.setRange(0, 100000)
        self.recharge_update = QComboBox()
        self.recharge_update.addItems(["(engine default)", "daily", "monthly", "monthly_ramped"])
        self.recharge_delay = QDoubleSpinBox()
        self.recharge_delay.setRange(-1.0, 100000.0)
        self.recharge_delay.setSpecialValueText("(engine default)")
        self.recharge_delay.setValue(-1.0)
        self.pumping_scale = QDoubleSpinBox()
        self.pumping_scale.setRange(-1.0, 1000.0)
        self.pumping_scale.setSpecialValueText("(engine default)")
        self.pumping_scale.setValue(-1.0)
        self.mb_tol = QDoubleSpinBox()
        self.mb_tol.setRange(-1.0, 5.0)
        self.mb_tol.setDecimals(4)
        self.mb_tol.setSpecialValueText("(engine default)")
        self.mb_tol.setValue(-1.0)
        self.out_dir = PathEdit("folder", title="Output folder")
        self.out_prefix = QLineEdit()
        self.out_prefix.setPlaceholderText("(engine default)")
        for label, w in (("Project name", self.name), ("CRS", self.crs), ("SWAT+ TxtInOut", self.txtinout),
                         ("SWAT+ executable", self.executable), ("", self.run_swatplus), ("MODFLOW 6 folder", self.sim_dir),
                         ("libmf6 library", self.libmf6), ("MODFLOW model name (optional)", self.model_name),
                         ("Starting heads (optional)", self.initial_heads),
                         ("hru_cells.csv", self.hru_cells), ("SFR reach table", self.channel_reaches),
                         ("channel_cells.csv (optional)", self.channel_cells), ("grid.json (optional)", self.grid_json),
                         ("Well cells (optional)", self.well_cells), ("Period start", self.start), ("Period end", self.end),
                         ("Spin-up days", self.spinup), ("Recharge update", self.recharge_update),
                         ("Recharge delay (days)", self.recharge_delay), ("Pumping scale", self.pumping_scale),
                         ("Mass-balance tolerance (%)", self.mb_tol), ("Output folder", self.out_dir),
                         ("Output file prefix", self.out_prefix)):
            f.addRow(label, w)

    def project_dict(self) -> dict:
        def opt(sb):
            return None if sb.value() < 0 else sb.value()
        base = os.path.dirname(os.path.abspath(self.project_path.text())) if self.project_path.text() else None
        ru = self.recharge_update.currentText()
        return project_file.build_project_dict(
            name=self.name.text().strip(), crs=self.crs.text().strip(), txtinout=self.txtinout.text(),
            sim_dir=self.sim_dir.text(), libmf6=self.libmf6.text(), hru_cells=self.hru_cells.text(),
            channel_reaches=self.channel_reaches.text(),
            start=self.start.date().toString(ISO), end=self.end.date().toString(ISO),
            executable=self.executable.text() or None, run_swatplus=self.run_swatplus.isChecked(),
            model_name=self.model_name.text().strip() or None,
            initial_heads=self.initial_heads.text() or None, well_cells=self.well_cells.text() or None,
            channel_cells=self.channel_cells.text() or None, grid=self.grid_json.text() or None,
            spinup_days=self.spinup.value(), recharge_update=None if ru.startswith("(") else ru,
            recharge_delay_days=opt(self.recharge_delay), pumping_scale=opt(self.pumping_scale),
            mass_balance_tolerance_percent=opt(self.mb_tol), output_directory=self.out_dir.text() or None,
            output_prefix=self.out_prefix.text() or None, base_dir=base)

    def fill_form(self, data: dict):
        sw, mfs, con, per = data.get("swatplus", {}), data.get("modflow", {}), data.get("connections", {}), data.get("period", {})
        cpl, chk, out = data.get("coupling", {}), data.get("checks", {}), data.get("output", {})
        self.name.setText(str(data.get("name", "")))
        self.crs.setText(str(data.get("crs", "")))
        self.txtinout.setText(str(sw.get("txtinout", "")))
        self.executable.setText(str(sw.get("executable", "") or ""))
        self.run_swatplus.setChecked(bool(sw.get("run_swatplus", True)))
        self.sim_dir.setText(str(mfs.get("sim_dir", "")))
        self.libmf6.setText(str(mfs.get("libmf6", "")))
        self.model_name.setText(str(mfs.get("model_name", "") or ""))
        self.initial_heads.setText(str(mfs.get("initial_heads", "") or ""))
        self.hru_cells.setText(str(con.get("hru_cells", "")))
        self.channel_reaches.setText(str(con.get("channel_reaches", "")))
        self.well_cells.setText(str(con.get("well_cells", "") or ""))
        self.channel_cells.setText(str(con.get("channel_cells", "") or ""))
        self.grid_json.setText(str(con.get("grid", "") or ""))
        for widget, key in ((self.start, "start"), (self.end, "end")):
            d = QDate.fromString(str(per.get(key, "")), ISO)
            if d.isValid():
                widget.setDate(d)
        self.spinup.setValue(int(per.get("spinup_days", 0)))
        idx = self.recharge_update.findText(str(cpl.get("recharge_update", "")))
        self.recharge_update.setCurrentIndex(max(idx, 0))
        self.recharge_delay.setValue(float(cpl.get("recharge_delay_days", -1.0)))
        self.pumping_scale.setValue(float(cpl.get("pumping_scale", -1.0)))
        self.mb_tol.setValue(float(chk.get("mass_balance_tolerance_percent", -1.0)))
        self.out_dir.setText(str(out.get("directory", "")))
        self.out_prefix.setText(str(out.get("prefix", "")))

    # -- actions ----------------------------------------------------------------
    def say(self, text: str):
        self.log.appendPlainText(text)

    def refresh_engine_label(self):
        py = settings.engine_python()
        self.engine_label.setText(py or "(not set: use Engine settings)")

    def _warn(self, text: str) -> None:
        QMessageBox.warning(self, "SWAT+ MODFLOW 6", text)

    def load_project(self):
        path = self.project_path.text()
        if not path or not os.path.exists(path):
            self._warn("Enter the path of an existing project file.")
            return
        try:
            data = project_file.read_project_json(path)
        except (OSError, ValueError) as exc:
            self.use_form.setChecked(False)
            self.say(f"{exc}")
            return
        self.fill_form(data)
        self.say(f"loaded {path}")

    def write_project(self) -> Optional[str]:
        """Write the form to the project file; returns the path or None (problems are shown)."""
        path = self.project_path.text()
        if not path:
            self._warn("Choose where to write the project file.")
            return None
        data = self.project_dict()
        problems = project_file.check_project_dict(data)
        if problems:
            self._warn("The project is incomplete:\n\n- " + "\n- ".join(problems))
            return None
        project_file.write_project_json(data, path)
        self.say(f"wrote {path}")
        return path

    def _prepare(self) -> Optional[tuple]:
        py = settings.engine_python()
        if not py or not os.path.exists(py):
            self._warn("Set the engine Python interpreter first (Engine settings).")
            return None
        path = self.write_project() if self.use_form.isChecked() else self.project_path.text()
        if not path or not os.path.exists(path):
            return None
        return py, os.path.abspath(path)

    def _start(self, args, intent: str):
        py, path = self._prep
        self.proc = EngineProcess(self)
        self.proc.outputLine.connect(self.say)
        self.proc.progress.connect(self._on_progress)
        self.proc.failedToStart.connect(self.say)
        self.proc.finishedRun.connect(lambda code, status, detail: self._finished(intent, code, status, detail))
        self._set_busy(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status.setText(f"{intent}...")
        self.say(f"$ {eng.format_command(py, args)}")
        self.proc.start(py, args, os.path.dirname(path), getattr(self, "extra_env", None))

    def validate(self):
        if self.proc is not None and self.proc.is_running():
            return
        prep = self._prepare()
        if prep:
            self._prep = prep
            self._start(eng.validate_args(prep[1]), "validate")

    def run(self):
        if self.proc is not None and self.proc.is_running():
            return
        prep = self._prepare()
        if prep:
            self._prep = prep
            self.last_manifest = None
            self._start(eng.run_args(prep[1]), "run")

    def cancel(self):
        if self.proc is not None and self.proc.is_running():
            self.status.setText("cancelling...")
            self.proc.cancel()

    def _on_progress(self, done, total, day):
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.status.setText(f"{done}/{total} {day}")

    def _set_busy(self, busy: bool):
        for b in (self.run_button, self.validate_button, self.save_button, self.load_button, self.results_button):
            b.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def _finished(self, intent, code, status, detail):
        self._set_busy(False)
        if status == "cancelled":
            self.status.setText("Cancelled")
            return
        if intent == "validate":
            errs, warns = eng.parse_findings("\n".join(self.proc.lines))
            self.status.setText(f"Validation: {len(errs)} error(s), {len(warns)} warning(s)" if code in (0, 1) else f"Engine exit code {code}")
            return
        if code == 0 and status == "succeeded":
            self.status.setText("Run succeeded")
            self.progress.setValue(self.progress.maximum())
            manifest = detail if os.path.isabs(detail) else os.path.join(os.path.dirname(self._prep[1]), detail)
            self.last_manifest = manifest
            self._load_manifest(manifest)
        else:
            reason = detail if status == "failed" else "no RESULT line"
            self.status.setText(f"Run failed (exit code {code}): {reason}")

    def _load_manifest(self, manifest: str):
        grid = self.grid_combo.currentLayer()
        if grid is None:
            self.say("no grid layer selected: cell-value files are loaded as plain tables")
        try:
            self.loaded_layers = results.load_manifest(manifest, grid, self.qgis_project)
            self.say(f"loaded {len(self.loaded_layers)} result layer(s) from {manifest}")
        except (OSError, ValueError, KeyError) as exc:
            self.say(f"could not load results: {exc}")

    def load_results_dialog(self):
        from qgis.PyQt.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "run_manifest.json", "", "Manifest (*.json)")
        if path:
            self._load_manifest(path)

    def shutdown(self):
        if self.proc is not None and self.proc.is_running():
            self.proc.cancel()
