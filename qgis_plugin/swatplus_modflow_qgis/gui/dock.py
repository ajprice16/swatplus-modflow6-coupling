"""Dock widget: shortcuts to the Processing tools, engine settings and the run dialog."""
from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QDockWidget, QLabel, QPushButton, QVBoxLayout, QWidget

# (button text, algorithm id)
TOOLS = [
    ("1. Create MODFLOW grid...", "swatplusmodflow:creategrid"),
    ("2. Build HRU connections...", "swatplusmodflow:hruconnections"),
    ("3. Build channel connections...", "swatplusmodflow:channelconnections"),
    ("4. Validate inputs...", "swatplusmodflow:validateinputs"),
]


class SwatPlusDock(QDockWidget):
    def __init__(self, iface, parent=None):
        super().__init__("SWAT+ MODFLOW 6", parent)
        self.setObjectName("SwatPlusModflowDock")
        self.iface = iface
        self._dialogs = []

        body = QWidget(self)
        layout = QVBoxLayout(body)
        intro = QLabel("Prepare the coupling inputs, then run the engine.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        for text, alg_id in TOOLS:
            btn = QPushButton(text)
            btn.clicked.connect(lambda _checked=False, a=alg_id: self.open_algorithm(a))
            layout.addWidget(btn)
        self.run_button = QPushButton("5. Project and run engine...")
        self.run_button.clicked.connect(self.open_run_dialog)
        layout.addWidget(self.run_button)
        self.settings_button = QPushButton("Engine settings...")
        self.settings_button.clicked.connect(self.open_settings_dialog)
        layout.addWidget(self.settings_button)
        layout.addStretch(1)
        self.setWidget(body)

    def open_algorithm(self, algorithm_id: str):
        from qgis import processing
        processing.execAlgorithmDialog(algorithm_id, {})

    def open_run_dialog(self):
        from .run_dialog import RunDialog
        dlg = RunDialog(self.iface, self.iface.mainWindow())
        self._remember(dlg)
        dlg.show()

    def open_settings_dialog(self):
        from .settings_dialog import EngineSettingsDialog
        dlg = EngineSettingsDialog(self.iface.mainWindow())
        dlg.exec()

    def _remember(self, dlg):
        self._dialogs = [d for d in self._dialogs if d.isVisible()]
        self._dialogs.append(dlg)

    def shutdown(self):
        for d in self._dialogs:
            if hasattr(d, "shutdown"):
                d.shutdown()
            d.close()
        self._dialogs = []
