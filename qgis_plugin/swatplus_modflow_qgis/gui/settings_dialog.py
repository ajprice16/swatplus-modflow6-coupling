"""Engine settings: interpreter path, 'Check engine', optional virtual-environment helper."""
from __future__ import annotations

import os

from qgis.PyQt.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                                 QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout)

from ..core import engine as eng
from . import settings
from .engine_process import CommandQueue
from .widgets import PathEdit


class EngineSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SWAT+ MODFLOW 6 - engine settings")
        self.resize(720, 620)
        self.queue = CommandQueue(self)
        self.queue.line.connect(self.log_line)
        self.queue.commandStarted.connect(lambda c: self.log_line(f"$ {c}"))
        self._after = None
        self.queue.allFinished.connect(self._queue_done)

        lay = QVBoxLayout(self)
        note = QLabel("The engine runs in its own Python environment (it needs flopy, MODFLOW 6 and so on). "
                      "Point the plugin at that environment's interpreter; QGIS itself never imports the engine.")
        note.setWordWrap(True)
        lay.addWidget(note)

        form = QFormLayout()
        self.python_edit = PathEdit("file", "All files (*)", "Engine Python interpreter")
        self.python_edit.setText(settings.engine_python())
        form.addRow("Engine Python interpreter", self.python_edit)
        self.project_edit = PathEdit("file", "Project files (*.json *.yaml *.yml);;All files (*)", "Project file to validate")
        form.addRow("Project file for 'validate' (optional)", self.project_edit)
        lay.addLayout(form)
        row = QHBoxLayout()
        self.check_button = QPushButton("Check engine")
        self.check_button.clicked.connect(self.check_engine)
        row.addWidget(self.check_button)
        row.addStretch(1)
        lay.addLayout(row)

        box = QGroupBox("Create an engine environment (optional)")
        bl = QFormLayout(box)
        self.base_python = PathEdit("file", "All files (*)", "Python used to create the environment")
        self.base_python.setText(settings.get(eng.SETTINGS_BASE_PYTHON_KEY))
        self.venv_dir = PathEdit("folder", title="Environment folder")
        self.venv_dir.setText(settings.get(eng.SETTINGS_VENV_DIR_KEY))
        self.package = QLineEdit()
        self.package.setPlaceholderText("local folder of the package, or a URL / 'git+https://...' requirement")
        self.package.setText(settings.get(eng.SETTINGS_PACKAGE_SPEC_KEY))
        bl.addRow("Python to build it with", self.base_python)
        bl.addRow("New environment folder", self.venv_dir)
        bl.addRow("Package to install", self.package)
        self.commands_view = QPlainTextEdit()
        self.commands_view.setReadOnly(True)
        self.commands_view.setMaximumHeight(90)
        bl.addRow("Commands that will run", self.commands_view)
        self.create_button = QPushButton("Create environment and install...")
        self.create_button.clicked.connect(self.create_environment)
        bl.addRow(self.create_button)
        lay.addWidget(box)
        for w in (self.base_python, self.venv_dir):
            w.textChanged.connect(lambda _t: self.refresh_commands())
        self.package.textChanged.connect(lambda _t: self.refresh_commands())
        self.refresh_commands()

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        lay.addWidget(self.log, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).clicked.connect(self.save)
        lay.addWidget(buttons)

    # ------------------------------------------------------------------
    def log_line(self, text: str):
        self.log.appendPlainText(text)

    def save(self):
        settings.set_value(eng.SETTINGS_PYTHON_KEY, self.python_edit.text())
        settings.set_value(eng.SETTINGS_BASE_PYTHON_KEY, self.base_python.text())
        settings.set_value(eng.SETTINGS_VENV_DIR_KEY, self.venv_dir.text())
        settings.set_value(eng.SETTINGS_PACKAGE_SPEC_KEY, self.package.text().strip())
        self.log_line("settings saved")

    def check_engine(self):
        py = self.python_edit.text()
        if not py or not os.path.exists(py):
            QMessageBox.warning(self, "Engine", "Choose an existing Python interpreter first.")
            return
        cmds = [(py, eng.version_args())]
        if self.project_edit.text():
            cmds.append((py, eng.validate_args(self.project_edit.text())))
        self.log.clear()
        self.check_button.setEnabled(False)
        self._after = "check"
        self.queue.start(cmds)

    def _commands(self):
        try:
            return eng.venv_commands(self.base_python.text(), self.venv_dir.text(), self.package.text())
        except ValueError:
            return None

    def refresh_commands(self):
        cmds = self._commands() if (self.base_python.text() and self.venv_dir.text()) else None
        self.commands_view.setPlainText("\n".join(eng.format_command(p, a) for p, a in cmds) if cmds else
                                        "(fill in the three fields above)")

    def create_environment(self):
        cmds = self._commands()
        if not cmds or not self.base_python.text() or not self.venv_dir.text():
            QMessageBox.warning(self, "Environment", "Fill in the interpreter, the environment folder and the package.")
            return
        text = "These commands will run, one after another:\n\n" + "\n".join(eng.format_command(p, a) for p, a in cmds)
        ans = QMessageBox.question(self, "Create environment", text,
                                   QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if ans != QMessageBox.StandardButton.Ok:
            return
        self.create_button.setEnabled(False)
        self._after = "venv"
        self.save()
        self.queue.start(cmds)

    def _queue_done(self, ok: bool):
        self.check_button.setEnabled(True)
        self.create_button.setEnabled(True)
        if self._after == "venv" and ok:
            py = eng.venv_python(self.venv_dir.text())
            self.python_edit.setText(py)
            self.log_line(f"environment ready; engine interpreter set to {py}")
            self.save()
        self.log_line("OK" if ok else "FAILED")

    def reject(self):
        self.queue.cancel()
        super().reject()
