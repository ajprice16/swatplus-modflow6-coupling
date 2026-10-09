"""Build HRU connections (hru_cells.csv)."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Tuple

from qgis.core import (Qgis, QgsGeometry, QgsProcessingAlgorithm, QgsProcessingException, QgsProcessingOutputNumber,
                       QgsProcessingParameterFeatureSource, QgsProcessingParameterField,
                       QgsProcessingParameterFileDestination, QgsProcessingParameterNumber, QgsRectangle)

from ..core import contract
from . import common

_MAX_LISTED = 40


class HruConnectionsAlgorithm(QgsProcessingAlgorithm):
    HRUS = "HRUS"
    ID_FIELD = "ID_FIELD"
    GRID = "GRID"
    LAYER = "LAYER"
    OUTPUT = "OUTPUT"

    def name(self): return "hruconnections"
    def displayName(self): return "Build HRU connections"
    def group(self): return common.GROUP_NAME
    def groupId(self): return common.GROUP_ID
    def createInstance(self): return HruConnectionsAlgorithm()

    def shortHelpString(self):
        return ("Intersects HRU polygons with the MODFLOW grid and writes hru_cells.csv "
                "(columns hru_id, layer, row, col, area_m2, fraction).\n\n"
                "area_m2 is the planar area of the HRU inside the cell; fraction = area_m2 divided by the total "
                "polygon area of that HRU (all polygons sharing the id), so an HRU that lies completely inside the "
                "grid has fractions that sum to 1. HRUs that are only partly inside the grid, or not at all, are "
                "listed in the log: their recharge leaves the model and fractions are NOT rescaled.\n\n"
                "The id field defaults to 'HRUS', the HRU id of the 'hrus2' layer written by QSWAT+. It must hold "
                "the SWAT+ HRU id (the id column of hru.con). The HRU layer and the grid must share one projected CRS.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.HRUS, "HRU polygon layer", [Qgis.ProcessingSourceType.VectorPolygon]))
        self.addParameter(QgsProcessingParameterField(
            self.ID_FIELD, "HRU id field", "HRUS", self.HRUS, Qgis.ProcessingFieldParameterDataType.Any))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.GRID, "MODFLOW grid layer (fields row, col, active)", [Qgis.ProcessingSourceType.VectorPolygon]))
        self.addParameter(QgsProcessingParameterNumber(
            self.LAYER, "MODFLOW layer that receives recharge", Qgis.ProcessingNumberParameterType.Integer, 1, minValue=1))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT, "hru_cells.csv", "CSV files (*.csv)"))
        for key, label in (("HRU_COUNT", "HRUs in layer"), ("PARTIAL_HRUS", "HRUs partly outside the grid"),
                           ("UNCOVERED_HRUS", "HRUs entirely outside the grid"), ("ROWS", "Rows written")):
            self.addOutput(QgsProcessingOutputNumber(key, label))

    def processAlgorithm(self, parameters, context, feedback):
        hrus = self.parameterAsSource(parameters, self.HRUS, context)
        grid_src = self.parameterAsSource(parameters, self.GRID, context)
        if hrus is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.HRUS))
        if grid_src is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.GRID))
        id_field = self.parameterAsString(parameters, self.ID_FIELD, context)
        layer_no = self.parameterAsInt(parameters, self.LAYER, context)
        out_path = self.parameterAsFileOutput(parameters, self.OUTPUT, context)

        authid = common.authid_of(grid_src.sourceCrs(), "Grid layer")
        common.require_same_crs(authid, "the grid", hrus, "HRU layer")
        common.require_fields(hrus, [id_field], "HRU layer")
        spec, active = common.grid_spec_from_source(grid_src, authid, feedback)
        feedback.pushInfo(f"Grid {spec.nrow} x {spec.ncol}, CRS {authid}")

        areas: Dict[Tuple[int, int, int], float] = defaultdict(float)
        poly_area: Dict[int, float] = defaultdict(float)
        n_polys: Dict[int, int] = defaultdict(int)
        total = hrus.featureCount() or 1
        cell_area = spec.delr * spec.delc
        for i, f in enumerate(hrus.getFeatures()):
            if feedback.isCanceled():
                break
            feedback.setProgress(100.0 * i / total)
            hid = common.as_int_id(f[id_field], "HRU layer", f.id())
            geom = f.geometry()
            if geom.isNull() or geom.isEmpty():
                feedback.reportError(f"HRU {hid} (feature {f.id()}) has no geometry and is skipped.", False)
                continue
            if not geom.isGeosValid():
                geom = geom.makeValid()
            a = geom.area()
            poly_area[hid] += a
            n_polys[hid] += 1
            bb = geom.boundingBox()
            rng = spec.cell_range(bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum())
            if rng is None:
                continue
            row0, row1, col0, col1 = rng
            engine = QgsGeometry.createGeometryEngine(geom.constGet())
            engine.prepareGeometry()
            for row in range(row0, row1 + 1):
                for col in range(col0, col1 + 1):
                    x0, y0, x1, y1 = spec.cell_bounds(row, col)
                    rect = QgsGeometry.fromRect(QgsRectangle(x0, y0, x1, y1))
                    if not engine.intersects(rect.constGet()):
                        continue
                    if engine.contains(rect.constGet()):
                        inter_area = cell_area
                    else:
                        inter_area = geom.intersection(rect).area()
                    if inter_area > 1e-9 * cell_area:
                        areas[(hid, row, col)] += inter_area

        rows = []
        covered: Dict[int, float] = defaultdict(float)
        in_inactive: Dict[int, float] = defaultdict(float)
        for (hid, row, col) in sorted(areas):
            a = areas[(hid, row, col)]
            total_area = poly_area[hid]
            if total_area <= 0:
                raise QgsProcessingException(f"HRU {hid} has zero polygon area")
            frac = a / total_area
            covered[hid] += frac
            if not active.get((row, col), 1):
                in_inactive[hid] += frac
            rows.append({"hru_id": hid, "layer": layer_no, "row": row, "col": col, "area_m2": a, "fraction": frac})
        n = contract.write_hru_cells(out_path, rows)

        tol = 1e-6
        uncovered = sorted(h for h in poly_area if h not in covered)
        partial = sorted(h for h in covered if covered[h] < 1.0 - tol)
        multi = sorted(h for h, k in n_polys.items() if k > 1)
        over = sorted(h for h in covered if covered[h] > 1.0 + 1e-4)
        if over:
            raise QgsProcessingException(f"HRU fractions exceed 1 for {over[:10]}: overlapping HRU polygons with the same id?")
        feedback.pushInfo(f"{len(poly_area)} HRUs, {n} HRU-cell rows written to {out_path}")
        if multi:
            feedback.pushInfo(f"{len(multi)} HRU id(s) consist of several polygons; their areas were combined.")
        if uncovered:
            feedback.reportError(
                f"{len(uncovered)} HRU(s) lie entirely outside the grid and have no rows (their recharge leaves the model): "
                + ", ".join(map(str, uncovered[:_MAX_LISTED])) + (" ..." if len(uncovered) > _MAX_LISTED else ""), False)
        if partial:
            worst = sorted(partial, key=lambda h: covered[h])[:_MAX_LISTED]
            feedback.reportError(
                f"{len(partial)} HRU(s) are only partly inside the grid (fractions sum to < 1; not rescaled): "
                + ", ".join(f"{h} ({covered[h]:.4f})" for h in worst) + (" ..." if len(partial) > _MAX_LISTED else ""), False)
        if in_inactive:
            feedback.pushWarning(
                f"{len(in_inactive)} HRU(s) have area in inactive grid cells (recharge assigned to cells outside the "
                "model domain): " + ", ".join(map(str, sorted(in_inactive)[:_MAX_LISTED])))
        return {self.OUTPUT: out_path, "HRU_COUNT": len(poly_area), "PARTIAL_HRUS": len(partial),
                "UNCOVERED_HRUS": len(uncovered), "ROWS": n}
