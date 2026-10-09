"""
QGIS Python-console script: build SWAT+ <-> MODFLOW connection features.

Works in QGIS 3.40+ and QGIS 4 (Qt5 or Qt6). For each HRU polygon it records
the fraction of the HRU's area inside each MODFLOW cell; for each channel line
it records the channel length inside each cell. The output GeoPackage has the
columns read by ``GeographicMapper``:

    swat_obj_type, swat_obj_id, mf_layer, mf_row, mf_col, fraction, length_m

Requirements on the input layers (all must be loaded in the project):

* HRU polygons with an integer HRU id field (QSWAT+ ``hrus2.shp`` uses ``HRUS``).
* Channel lines with an integer channel id field (QSWAT+ ``rivs1.shp`` uses
  ``Channel``) -- optional.
* MODFLOW grid polygons carrying 1-based ``row`` and ``col`` fields (the
  smalltest builder and the plugin's grid tool write these).
* All three layers in the same projected CRS (lengths/areas in metres).

Usage: Plugins > Python Console > Show Editor > open this file, edit the
PARAMETERS block, Run.
"""

from qgis.core import (
    QgsFeature,
    QgsField,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QMetaType
import processing

# ============================================================
# PARAMETERS -- edit for your project
# ============================================================
HRU_LAYER_NAME = "hrus2"
HRU_ID_FIELD = "HRUS"
CHANNEL_LAYER_NAME = "rivs1"          # set to None to skip channels
CHANNEL_ID_FIELD = "Channel"
GRID_LAYER_NAME = "modflow_grid"
GRID_ROW_FIELD = "row"
GRID_COL_FIELD = "col"
OUTPUT_FILE = "/path/to/connections.gpkg"
MIN_FRACTION = 0.0                     # drop HRU-cell slivers below this fraction
MF_LAYER = 1                           # MODFLOW layer receiving the exchange (1-based)
# ============================================================


def _layer(name):
    found = QgsProject.instance().mapLayersByName(name)
    if not found:
        raise RuntimeError(f"Layer {name!r} is not loaded in the project")
    return found[0]


def _check_inputs(hru, grid, chan):
    for lyr in (hru, grid) + ((chan,) if chan else ()):
        if lyr.crs() != hru.crs():
            raise RuntimeError(f"CRS mismatch: {lyr.name()} is {lyr.crs().authid()}, HRUs are {hru.crs().authid()}")
        if lyr.crs().isGeographic():
            raise RuntimeError(f"{lyr.name()} is in a geographic CRS; reproject to a projected CRS in metres")
    for fld in (GRID_ROW_FIELD, GRID_COL_FIELD):
        if grid.fields().indexOf(fld) < 0:
            raise RuntimeError(f"Grid layer has no {fld!r} field")
    if hru.fields().indexOf(HRU_ID_FIELD) < 0:
        raise RuntimeError(f"HRU layer has no {HRU_ID_FIELD!r} field")
    if chan and chan.fields().indexOf(CHANNEL_ID_FIELD) < 0:
        raise RuntimeError(f"Channel layer has no {CHANNEL_ID_FIELD!r} field")


def _intersect(inp, grid, inp_field):
    return processing.run("native:intersection", {
        "INPUT": inp,
        "OVERLAY": grid,
        "INPUT_FIELDS": [inp_field],
        "OVERLAY_FIELDS": [GRID_ROW_FIELD, GRID_COL_FIELD],
        "OUTPUT": "memory:",
    })["OUTPUT"]


def create_connections():
    hru = _layer(HRU_LAYER_NAME)
    grid = _layer(GRID_LAYER_NAME)
    chan = _layer(CHANNEL_LAYER_NAME) if CHANNEL_LAYER_NAME else None
    _check_inputs(hru, grid, chan)

    out = QgsVectorLayer(f"MultiPolygon?crs={hru.crs().authid()}", "connections", "memory")
    out.dataProvider().addAttributes([
        QgsField("swat_obj_type", QMetaType.Type.QString),
        QgsField("swat_obj_id", QMetaType.Type.Int),
        QgsField("mf_layer", QMetaType.Type.Int),
        QgsField("mf_row", QMetaType.Type.Int),
        QgsField("mf_col", QMetaType.Type.Int),
        QgsField("fraction", QMetaType.Type.Double),
        QgsField("length_m", QMetaType.Type.Double),
    ])
    out.updateFields()

    # HRU area fractions. Fractions are relative to the full HRU area, so an HRU
    # partly outside the grid sums to < 1 -- that water is reported, not hidden.
    hru_area = {}
    for f in hru.getFeatures():
        hid = int(f[HRU_ID_FIELD])
        hru_area[hid] = hru_area.get(hid, 0.0) + f.geometry().area()
    feats, dropped = [], 0.0
    for f in _intersect(hru, grid, HRU_ID_FIELD).getFeatures():
        hid = int(f[HRU_ID_FIELD])
        frac = f.geometry().area() / hru_area[hid]
        if frac < MIN_FRACTION:
            dropped += frac
            continue
        nf = QgsFeature(out.fields())
        nf.setGeometry(f.geometry())
        nf.setAttributes(["hru", hid, MF_LAYER, int(f[GRID_ROW_FIELD]), int(f[GRID_COL_FIELD]), frac, None])
        feats.append(nf)
    out.dataProvider().addFeatures(feats)
    covered = {}
    for nf in feats:
        covered[nf["swat_obj_id"]] = covered.get(nf["swat_obj_id"], 0.0) + nf["fraction"]
    partial = {h: c for h, c in covered.items() if c < 0.999}
    missing = set(hru_area) - set(covered)
    print(f"HRU connections: {len(feats)} (slivers dropped: total fraction {dropped:.4f})")
    if partial or missing:
        print(f"WARNING: {len(partial)} HRUs partly and {len(missing)} HRUs entirely outside the grid")

    # Channel lengths are written as attributes on the intersected cell pieces;
    # the output layer is polygon, so channel rows carry no geometry.
    if chan:
        n = 0
        for f in _intersect(chan, grid, CHANNEL_ID_FIELD).getFeatures():
            if QgsWkbTypes.geometryType(f.geometry().wkbType()) != QgsWkbTypes.GeometryType.LineGeometry:
                continue
            nf = QgsFeature(out.fields())
            nf.setAttributes(["channel", int(f[CHANNEL_ID_FIELD]), MF_LAYER,
                              int(f[GRID_ROW_FIELD]), int(f[GRID_COL_FIELD]), None, f.geometry().length()])
            out.dataProvider().addFeatures([nf])
            n += 1
        print(f"Channel connections: {n}")

    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.fileEncoding = "UTF-8"
    err = QgsVectorFileWriter.writeAsVectorFormatV3(out, OUTPUT_FILE, QgsProject.instance().transformContext(), opts)
    if err[0] != QgsVectorFileWriter.WriterError.NoError:
        raise RuntimeError(f"Writing {OUTPUT_FILE} failed: {err[1]}")
    print(f"Saved {OUTPUT_FILE}")
    QgsProject.instance().addMapLayer(QgsVectorLayer(OUTPUT_FILE, "connections", "ogr"))


create_connections()
