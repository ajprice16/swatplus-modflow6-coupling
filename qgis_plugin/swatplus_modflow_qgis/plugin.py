"""Plugin entry point: Processing provider, toolbar action, dock widget."""
from __future__ import annotations

import os

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon

try:  # Qt6 keeps QAction in QtGui, Qt5 in QtWidgets
    from qgis.PyQt.QtGui import QAction
except ImportError:  # pragma: no cover - Qt5 only
    from qgis.PyQt.QtWidgets import QAction

from .provider import SwatPlusModflowProvider

PLUGIN_DIR = os.path.dirname(__file__)
MENU = "&SWAT+ MODFLOW 6"


class SwatPlusModflowPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.dock = None
        self.action = None
        self.run_action = None

    # -- QGIS plugin interface ---------------------------------------------
    def initProcessing(self):  # noqa: N802
        self.provider = SwatPlusModflowProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802
        self.initProcessing()
        icon = QIcon(os.path.join(PLUGIN_DIR, "icon.svg"))
        self.action = QAction(icon, "SWAT+ MODFLOW 6 panel", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.toggled.connect(self._toggle_dock)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(MENU, self.action)

        from .gui.dock import SwatPlusDock
        self.dock = SwatPlusDock(self.iface, self.iface.mainWindow())
        self.dock.visibilityChanged.connect(self._dock_visibility)
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.dock.hide()

    def unload(self):
        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu(MENU, self.action)
            self.action.deleteLater()
            self.action = None
        if self.dock is not None:
            self.dock.shutdown()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    # -- helpers -------------------------------------------------------------
    def _toggle_dock(self, visible: bool):
        if self.dock is not None:
            self.dock.setVisible(visible)

    def _dock_visibility(self, visible: bool):
        if self.action is not None and self.action.isChecked() != visible:
            self.action.blockSignals(True)
            self.action.setChecked(visible)
            self.action.blockSignals(False)
