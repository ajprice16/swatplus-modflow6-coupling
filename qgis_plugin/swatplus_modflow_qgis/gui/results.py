"""Load a run manifest into QGIS: tables as layers, cell values joined to the grid layer on row/col."""
from __future__ import annotations

import os
from typing import List, Optional

from qgis.core import (QgsFeature, QgsField, QgsFields, QgsGeometry, QgsProject, QgsVectorFileWriter, QgsVectorLayer)
from qgis.PyQt.QtCore import QMetaType, QUrl

from ..core import manifest as mf


def _enum_int(value) -> int:
    return int(getattr(value, "value", value))


def load_table(path: str, name: str) -> QgsVectorLayer:
    """CSV as a geometry-less layer; the OGR CSV driver detects column types."""
    layer = QgsVectorLayer(f"{path}|option:AUTODETECT_TYPE=YES", name, "ogr")
    if not layer.isValid():  # delimited-text provider as a fallback
        uri = QUrl.fromLocalFile(path).toString() + "?type=csv&detectTypes=yes&geomType=none&subsetIndex=no&watchFile=no"
        layer = QgsVectorLayer(uri, name, "delimitedtext")
    if not layer.isValid():
        raise ValueError(f"cannot read table {path}")
    return layer


def grid_values_layer(grid_layer: QgsVectorLayer, values, name: str, date: Optional[str], layer_no: int) -> QgsVectorLayer:
    """Copy of the grid polygons with a ``value`` attribute looked up on (row, col)."""
    out = QgsVectorLayer(f"Polygon?crs={grid_layer.crs().authid()}", name, "memory")
    pr = out.dataProvider()
    pr.addAttributes([QgsField("row", QMetaType.Type.Int), QgsField("col", QMetaType.Type.Int),
                      QgsField("layer", QMetaType.Type.Int), QgsField("value", QMetaType.Type.Double),
                      QgsField("date", QMetaType.Type.QString)])
    out.updateFields()
    feats = []
    for f in grid_layer.getFeatures():
        key = (int(f["row"]), int(f["col"]))
        if key not in values:
            continue
        nf = QgsFeature(out.fields())
        nf.setGeometry(QgsGeometry(f.geometry()))
        nf.setAttributes([key[0], key[1], layer_no, values[key], date or ""])
        feats.append(nf)
    pr.addFeatures(feats)
    out.updateExtents()
    return out


def persist_gpkg(layer: QgsVectorLayer, path: str, layer_name: str) -> QgsVectorLayer:
    """Write a memory layer to a GeoPackage and return the file-backed layer; fall back to the memory layer."""
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = layer_name
    opts.actionOnExistingFile = QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
    res = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, QgsProject.instance().transformContext(), opts)
    if _enum_int(res[0]) != 0:
        return layer
    stored = QgsVectorLayer(f"{path}|layername={layer_name}", layer.name(), "ogr")
    return stored if stored.isValid() else layer


def load_manifest(manifest_path: str, grid_layer: Optional[QgsVectorLayer] = None,
                  project: Optional[QgsProject] = None, persist: bool = True) -> List[QgsVectorLayer]:
    """Add every file of the manifest to the project (inside a layer group) and return the layers.

    ``cell_values`` files need ``grid_layer`` (polygons with integer ``row``/``col``): one polygon
    layer per MODFLOW layer is built, holding the values of the latest date when the file has dates.
    Without a grid layer they are loaded as plain tables.
    """
    project = project or QgsProject.instance()
    man = mf.read_manifest(manifest_path)
    group = project.layerTreeRoot().insertGroup(0, f"SWAT+ MODFLOW 6 results: {man.project or os.path.basename(man.folder)}")
    layers: List[QgsVectorLayer] = []

    def add(layer):
        project.addMapLayer(layer, False)
        group.addLayer(layer)
        layers.append(layer)

    for entry in man.files:
        label = f"{entry.name} [{entry.units}]" if entry.units else entry.name
        if entry.kind == "table" or grid_layer is None:
            add(load_table(entry.path, label))
            continue
        rows, _has_date = mf.read_cell_values(entry.path)
        by_layer, latest = mf.latest_by_layer(rows)
        for lno in sorted(by_layer):
            lname = label if len(by_layer) == 1 else f"{label} L{lno}"
            lyr = grid_values_layer(grid_layer, by_layer[lno], lname, latest, lno)
            if persist:
                gpkg = os.path.join(man.folder, f"{entry.name}_L{lno}.gpkg")
                lyr = persist_gpkg(lyr, gpkg, f"{entry.name}_L{lno}")
                lyr.setName(lname)
            add(lyr)
    return layers
