"""Processing provider "SWAT+ MODFLOW 6"."""
from __future__ import annotations

import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon


class SwatPlusModflowProvider(QgsProcessingProvider):
    def id(self):
        return "swatplusmodflow"

    def name(self):
        return "SWAT+ MODFLOW 6"

    def longName(self):  # noqa: N802
        return "SWAT+ MODFLOW 6 coupling preparation"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))

    def loadAlgorithms(self):  # noqa: N802
        from .processing import ALGORITHMS
        for cls in ALGORITHMS:
            self.addAlgorithm(cls())
