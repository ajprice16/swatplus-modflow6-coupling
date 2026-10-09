"""Headless QGIS bootstrap and synthetic layers for the plugin tests.

Run through ``qgis_plugin/tests/run_qgis_tests.sh`` which sets the environment
of a QGIS installation. Nothing here refers to a real study area: coordinates
and the CRS are arbitrary and only exercise the geometry code.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PLUGIN_PARENT = Path(__file__).resolve().parents[2]
REPO = PLUGIN_PARENT.parent
for _p in (PLUGIN_PARENT, REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsFeature, QgsField, QgsGeometry, QgsPointXY,
                       QgsProject, QgsRasterLayer, QgsVectorLayer)

FAKE_ENGINE = REPO_FAKE = Path(__file__).resolve().parents[1] / "fake_engine"
from qgis.PyQt.QtCore import QMetaType

CRS = "EPSG:32633"   # arbitrary projected CRS
GEO_CRS = "EPSG:4326"
X0, Y0, D, NROW, NCOL = 1000.0, 2000.0, 100.0, 4, 5   # lower-left origin, cell size, grid shape

_qgs = None
_profile = None
_provider = None  # keep the Python wrapper alive


def start_qgis():
    """Initialise QgsApplication (once) with a throw-away profile and the Processing framework."""
    global _qgs, _profile
    if _qgs is not None:
        return _qgs
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _profile = tempfile.mkdtemp(prefix="swatplus_qgis_test_profile_")
    os.environ["QGIS_CUSTOM_CONFIG_PATH"] = _profile
    _qgs = QgsApplication([], True)   # GUI mode: the dialog tests create widgets (offscreen platform)
    _qgs.initQgis()
    plugins = os.path.join(QgsApplication.pkgDataPath(), "python", "plugins")
    if plugins not in sys.path:
        sys.path.append(plugins)
    from processing.core.Processing import Processing
    Processing.initialize()
    return _qgs


def register_provider():
    global _provider
    from swatplus_modflow_qgis.provider import SwatPlusModflowProvider
    reg = QgsApplication.processingRegistry()
    prov = reg.providerById("swatplusmodflow")
    if prov is None:
        prov = SwatPlusModflowProvider()
        reg.addProvider(prov)
    _provider = prov
    return prov


def run_alg(alg_id, params):
    import processing
    return processing.run(alg_id, params)


def mem_layer(geom_type, crs, fields, name="mem"):
    lyr = QgsVectorLayer(f"{geom_type}?crs={crs}", name, "memory")
    assert lyr.isValid(), (geom_type, crs)
    pr = lyr.dataProvider()
    pr.addAttributes([QgsField(n, t) for n, t in fields])
    lyr.updateFields()
    return lyr


def add_feature(lyr, wkt, attrs):
    f = QgsFeature(lyr.fields())
    f.setGeometry(QgsGeometry.fromWkt(wkt))
    f.setAttributes(list(attrs))
    assert lyr.dataProvider().addFeature(f)
    lyr.updateExtents()


def rect_wkt(x0, y0, x1, y1):
    return f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"


def domain_layer(crs=CRS):
    """L-shaped domain: lower-right corner of the bbox removed."""
    lyr = mem_layer("Polygon", crs, [("name", QMetaType.Type.QString)], "domain")
    add_feature(lyr, "POLYGON((1013 2021,1388 2021,1388 2150,1250 2150,1250 2290,1013 2290,1013 2021))", ["d"])
    return lyr


def hru_layer(id_type="int", crs=CRS):
    """HRUs on the 4 x 5 grid (origin X0,Y0; cell D):
    1 fills cells (row 1, cols 1-2); 2 is a triangle inside (row 2-3, col 3);
    3 sticks 50 m out of the west edge; 4 lies outside; 5 has two polygons (row 4).
    """
    ftype = QMetaType.Type.Int if id_type == "int" else QMetaType.Type.QString
    lyr = mem_layer("Polygon", crs, [("HRUS", ftype), ("Landuse", QMetaType.Type.QString)], "hrus2")
    conv = (lambda v: v) if id_type == "int" else (lambda v: str(v))
    top = Y0 + NROW * D
    add_feature(lyr, rect_wkt(1000, top - 100, 1200, top), [conv(1), "a"])
    add_feature(lyr, f"POLYGON((1200 {top - 200},1300 {top - 200},1200 {top - 100},1200 {top - 200}))", [conv(2), "a"])
    add_feature(lyr, rect_wkt(950, top - 300, 1100, top - 200), [conv(3), "b"])
    add_feature(lyr, rect_wkt(5000, 5000, 5100, 5100), [conv(4), "b"])
    add_feature(lyr, rect_wkt(1000, Y0, 1050, Y0 + 100), [conv(5), "c"])
    add_feature(lyr, rect_wkt(1450, Y0, 1500, Y0 + 100), [conv(5), "c"])
    return lyr


def line_wkt(pts):
    return "LINESTRING(" + ",".join(f"{x} {y}" for x, y in pts) + ")"


def channel_layer(crs=CRS):
    """Channels (field 'Channel') on the 4 x 5 grid:
    1: west->east inside row 2;
    2: multipart (two parts meeting at a junction, one pointing north, one south) running down col 2;
    3: west->east, leaves the grid across the east edge;  4: wholly outside.
    """
    top = Y0 + NROW * D     # 2400
    lyr = mem_layer("MultiLineString", crs, [("Channel", QMetaType.Type.Int)], "rivs1")
    add_feature(lyr, f"MULTILINESTRING((1010 {top - 150},1190 {top - 150}))", [1])
    add_feature(lyr, "MULTILINESTRING((1150 2250,1150 2100),(1150 2250,1150 2350))", [2])
    add_feature(lyr, f"MULTILINESTRING((1400 {top - 350},1500 {top - 350},1650 {top - 350}))", [3])
    add_feature(lyr, "MULTILINESTRING((8000 8000,8100 8000))", [4])
    return lyr


def write_dem(path, crs_wkt=None):
    """ASCII-grid DEM over the model grid, elevation falling to the south (row 4 lowest)."""
    nrows, ncols, cs = NROW * 2, NCOL * 2, D / 2
    top = Y0 + NROW * D
    with open(path, "w") as fh:
        fh.write(f"ncols {ncols}\nnrows {nrows}\nxllcorner {X0}\nyllcorner {Y0}\ncellsize {cs}\nNODATA_value -9999\n")
        for r in range(nrows):
            fh.write(" ".join(f"{100.0 - r * 2.0}" for _ in range(ncols)) + "\n")
    lyr = QgsRasterLayer(str(path), "dem")
    lyr.setCrs(QgsCoordinateReferenceSystem(CRS))
    return lyr


def rect_domain(crs=CRS):
    """Rectangle exactly spanning the 4 x 5 grid."""
    lyr = mem_layer("Polygon", crs, [("name", QMetaType.Type.QString)], "domain")
    add_feature(lyr, rect_wkt(X0, Y0, X0 + NCOL * D, Y0 + NROW * D), ["d"])
    return lyr


def grid_layer_from_alg(workdir, domain=None, size=D, buffer_=0.0):
    """Run 'Create MODFLOW grid' (default: on the rectangle) and return (gpkg layer, grid.json path)."""
    domain = domain or rect_domain()
    gpkg, js = str(Path(workdir) / "grid.gpkg"), str(Path(workdir) / "grid.json")
    out = run_alg("swatplusmodflow:creategrid", {"DOMAIN": domain, "CELL_SIZE": size, "BUFFER": buffer_, "SNAP": True,
                                                 "OUTPUT_GRID": gpkg, "OUTPUT_JSON": js})
    lyr = QgsVectorLayer(out["OUTPUT_GRID"], "grid", "ogr")
    assert lyr.isValid()
    return lyr, js
