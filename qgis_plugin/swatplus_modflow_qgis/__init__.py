"""SWAT+ MODFLOW 6 Coupling - QGIS plugin (QGIS 3.40+ on Qt5 and QGIS 4 on Qt6).

Importing this package must not import ``qgis`` so that the stdlib-only
``core`` modules can be tested with a plain interpreter; QGIS-dependent
modules are imported inside ``classFactory``.
"""

__version__ = "0.1.0"


def classFactory(iface):  # noqa: N802 - name required by QGIS
    from .plugin import SwatPlusModflowPlugin
    return SwatPlusModflowPlugin(iface)
