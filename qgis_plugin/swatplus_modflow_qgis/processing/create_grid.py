"""Create MODFLOW grid."""
from __future__ import annotations

from qgis.core import (Qgis, QgsFeature, QgsFields, QgsField, QgsGeometry, QgsProcessing, QgsProcessingAlgorithm,
                       QgsProcessingException, QgsProcessingOutputNumber, QgsProcessingParameterBoolean, QgsProcessingParameterFeatureSink,
                       QgsProcessingParameterFeatureSource, QgsProcessingParameterFileDestination,
                       QgsProcessingParameterNumber, QgsRectangle, QgsWkbTypes)
from qgis.PyQt.QtCore import QMetaType

from ..core import grid as gridmod
from . import common


class CreateGridAlgorithm(QgsProcessingAlgorithm):
    DOMAIN = "DOMAIN"
    CELL_SIZE = "CELL_SIZE"
    BUFFER = "BUFFER"
    SNAP = "SNAP"
    OUTPUT_GRID = "OUTPUT_GRID"
    OUTPUT_JSON = "OUTPUT_JSON"

    def name(self): return "creategrid"
    def displayName(self): return "Create MODFLOW grid"
    def group(self): return common.GROUP_NAME
    def groupId(self): return common.GROUP_ID
    def createInstance(self): return CreateGridAlgorithm()

    def shortHelpString(self):
        return ("Builds the uniform, unrotated MODFLOW grid that covers the model domain polygon(s).\n\n"
                "Cells are square; row 1 is the northernmost row and column 1 the westernmost; the origin "
                "(xorigin, yorigin) is the lower-left corner. The grid layer has integer fields row, col (1-based) "
                "and active (1 when the cell overlaps the domain, 0 otherwise). grid.json records CRS, origin, "
                "cell size and dimensions (format swatplus-modflow-grid/1).\n\n"
                "The domain layer must be in a projected CRS in metres. The cell size has no default; choose it "
                "for your model. With 'snap', the origin is rounded down to a multiple of the cell size.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.DOMAIN, "Model domain polygon layer", [Qgis.ProcessingSourceType.VectorPolygon]))
        self.addParameter(QgsProcessingParameterNumber(
            self.CELL_SIZE, "Cell size (m)", Qgis.ProcessingNumberParameterType.Double, minValue=1e-6))
        self.addParameter(QgsProcessingParameterNumber(
            self.BUFFER, "Buffer around the domain (m)", Qgis.ProcessingNumberParameterType.Double, 0.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SNAP, "Snap the origin to a multiple of the cell size", True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_GRID, "Grid (GeoPackage)", Qgis.ProcessingSourceType.VectorPolygon))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_JSON, "grid.json", "JSON files (*.json)"))
        for key, label in (("NROW", "Rows"), ("NCOL", "Columns"), ("ACTIVE_CELLS", "Active cells")):
            self.addOutput(QgsProcessingOutputNumber(key, label))

    def checkParameterValues(self, parameters, context):
        size = self.parameterAsDouble(parameters, self.CELL_SIZE, context)
        if not size > 0:
            return False, "Enter a cell size greater than zero."
        return super().checkParameterValues(parameters, context)

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.DOMAIN, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.DOMAIN))
        size = self.parameterAsDouble(parameters, self.CELL_SIZE, context)
        buffer = self.parameterAsDouble(parameters, self.BUFFER, context)
        snap = self.parameterAsBool(parameters, self.SNAP, context)
        json_path = self.parameterAsFileOutput(parameters, self.OUTPUT_JSON, context)
        authid = common.authid_of(source.sourceCrs(), "Domain layer")

        geoms = []
        for f in source.getFeatures():
            g = f.geometry()
            if g.isNull() or g.isEmpty():
                continue
            if not g.isGeosValid():
                g = g.makeValid()
            geoms.append(g)
        if not geoms:
            raise QgsProcessingException("Domain layer has no usable polygon geometry")
        domain = QgsGeometry.unaryUnion(geoms)
        bb = domain.boundingBox()
        try:
            spec = gridmod.grid_from_extent(authid, bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum(),
                                            size, buffer, snap)
        except gridmod.GridError as exc:
            raise QgsProcessingException(str(exc)) from exc
        feedback.pushInfo(f"Grid: {spec.nrow} rows x {spec.ncol} cols = {spec.ncells} cells of {size:g} m, "
                          f"lower-left origin ({spec.xorigin:.6f}, {spec.yorigin:.6f}), CRS {authid}")

        fields = QgsFields()
        for name in ("row", "col", "active"):
            fields.append(QgsField(name, QMetaType.Type.Int))
        sink, dest_id = self.parameterAsSink(parameters, self.OUTPUT_GRID, context, fields,
                                             Qgis.WkbType.Polygon, source.sourceCrs())
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT_GRID))

        engine = QgsGeometry.createGeometryEngine(domain.constGet())
        engine.prepareGeometry()
        n_active = 0
        for row in range(1, spec.nrow + 1):
            if feedback.isCanceled():
                break
            batch = []
            for col in range(1, spec.ncol + 1):
                x0, y0, x1, y1 = spec.cell_bounds(row, col)
                rect = QgsGeometry.fromRect(QgsRectangle(x0, y0, x1, y1))
                # overlap of positive area; cells that only touch the domain boundary stay inactive
                active = 1 if (engine.intersects(rect.constGet()) and not engine.touches(rect.constGet())) else 0
                n_active += active
                feat = QgsFeature(fields)
                feat.setGeometry(rect)
                feat.setAttributes([row, col, active])
                batch.append(feat)
            sink.addFeatures(batch)
            feedback.setProgress(100.0 * row / spec.nrow)
        if n_active == 0:
            feedback.reportError("No grid cell overlaps the domain; check the domain layer and cell size.", False)
        gridmod.write_grid_json(spec, json_path)
        feedback.pushInfo(f"{n_active} active cells; wrote {json_path}")
        return {self.OUTPUT_GRID: dest_id, self.OUTPUT_JSON: json_path,
                "NROW": spec.nrow, "NCOL": spec.ncol, "ACTIVE_CELLS": n_active}
