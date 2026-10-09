"""Validate inputs: CRS, grid, coverage, ids, connection tables -> readable report."""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Dict, List, Optional

from qgis.core import (Qgis, QgsGeometry, QgsProcessingAlgorithm, QgsProcessingException, QgsProcessingOutputNumber,
                       QgsProcessingOutputString, QgsProcessingParameterFeatureSource, QgsProcessingParameterField,
                       QgsProcessingParameterFile, QgsProcessingParameterFileDestination, QgsRectangle)

from ..core import contract, validation
from ..core import grid as gridmod
from . import common

_MAX_LISTED = 25


class ValidateInputsAlgorithm(QgsProcessingAlgorithm):
    GRID = "GRID"
    GRID_JSON = "GRID_JSON"
    DOMAIN = "DOMAIN"
    HRUS = "HRUS"
    HRU_ID_FIELD = "HRU_ID_FIELD"
    CHANNELS = "CHANNELS"
    CHANNEL_ID_FIELD = "CHANNEL_ID_FIELD"
    HRU_CELLS = "HRU_CELLS"
    CHANNEL_CELLS = "CHANNEL_CELLS"
    REPORT = "REPORT"

    def name(self): return "validateinputs"
    def displayName(self): return "Validate inputs"
    def group(self): return common.GROUP_NAME
    def groupId(self): return common.GROUP_ID
    def createInstance(self): return ValidateInputsAlgorithm()

    def shortHelpString(self):
        return ("Checks the coupling inputs before a run and writes a readable report (also shown in the log).\n\n"
                "Checks: every layer has a projected CRS and all CRSs are identical; the grid layer follows the "
                "uniform, unrotated grid with row 1 in the north and col 1 in the west, is complete and agrees with "
                "grid.json; the grid covers the domain and the 'active' flag matches the domain overlap; HRU and "
                "channel ids are present, integer and (for HRUs) combine into polygon areas; and, when the "
                "connection tables are given, that HRU fractions and channel fractions sum to 1 (partial coverage is "
                "a warning because that water leaves the model), seq runs 1..n, cells lie inside the grid and "
                "areas/lengths agree with the layers.\n\nAll inputs except the grid layer are optional; "
                "only what is supplied is checked.")

    def initAlgorithm(self, config=None):
        poly = [Qgis.ProcessingSourceType.VectorPolygon]
        self.addParameter(QgsProcessingParameterFeatureSource(self.GRID, "MODFLOW grid layer", poly))
        self.addParameter(QgsProcessingParameterFile(self.GRID_JSON, "grid.json", extension="json", optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(self.DOMAIN, "Model domain polygon layer", poly, optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(self.HRUS, "HRU polygon layer", poly, optional=True))
        self.addParameter(QgsProcessingParameterField(
            self.HRU_ID_FIELD, "HRU id field", "HRUS", self.HRUS, Qgis.ProcessingFieldParameterDataType.Any, optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.CHANNELS, "Channel line layer", [Qgis.ProcessingSourceType.VectorLine], optional=True))
        self.addParameter(QgsProcessingParameterField(
            self.CHANNEL_ID_FIELD, "Channel id field", "Channel", self.CHANNELS, Qgis.ProcessingFieldParameterDataType.Any, optional=True))
        self.addParameter(QgsProcessingParameterFile(self.HRU_CELLS, "hru_cells.csv", extension="csv", optional=True))
        self.addParameter(QgsProcessingParameterFile(self.CHANNEL_CELLS, "channel_cells.csv", extension="csv", optional=True))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.REPORT, "Report (HTML, optional)", "HTML files (*.html)", optional=True, createByDefault=False))
        self.addOutput(QgsProcessingOutputNumber("ERRORS", "Errors"))
        self.addOutput(QgsProcessingOutputNumber("WARNINGS", "Warnings"))
        self.addOutput(QgsProcessingOutputString("REPORT_TEXT", "Report text"))

    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        rep = validation.Report()
        grid_src = self.parameterAsSource(parameters, self.GRID, context)
        if grid_src is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.GRID))
        domain = self.parameterAsSource(parameters, self.DOMAIN, context)
        hrus = self.parameterAsSource(parameters, self.HRUS, context)
        chans = self.parameterAsSource(parameters, self.CHANNELS, context)
        hru_field = self.parameterAsString(parameters, self.HRU_ID_FIELD, context) or "HRUS"
        chan_field = self.parameterAsString(parameters, self.CHANNEL_ID_FIELD, context) or "Channel"
        grid_json = self.parameterAsFile(parameters, self.GRID_JSON, context) or None
        hru_csv = self.parameterAsFile(parameters, self.HRU_CELLS, context) or None
        chan_csv = self.parameterAsFile(parameters, self.CHANNEL_CELLS, context) or None

        # 1. CRS ---------------------------------------------------------------
        authids: Dict[str, str] = {}
        for label, src in (("Grid layer", grid_src), ("Domain layer", domain), ("HRU layer", hrus), ("Channel layer", chans)):
            if src is None:
                continue
            crs = src.sourceCrs()
            if not crs.isValid():
                rep.error("crs-invalid", f"{label} has no valid CRS")
            elif crs.isGeographic():
                rep.error("crs-geographic", f"{label} uses a geographic CRS ({crs.authid()}); a projected CRS in metres is required")
            elif not crs.authid() or ":" not in crs.authid():
                rep.error("crs-no-authority", f"{label} uses a CRS without an authority code ('{crs.description()}')")
            else:
                authids[label] = crs.authid()
                if crs.mapUnits() != Qgis.DistanceUnit.Meters:
                    rep.error("crs-units", f"{label}: CRS {crs.authid()} is not in metres (engine assumes metres)")
        if len(set(authids.values())) > 1:
            rep.error("crs-mismatch", "layers use different CRSs: " + ", ".join(f"{k} = {v}" for k, v in authids.items()))
        elif authids:
            rep.info("crs", f"all layers share {next(iter(authids.values()))}")
        authid = authids.get("Grid layer")

        # 2. grid layer ----------------------------------------------------------
        spec: Optional[gridmod.GridSpec] = None
        active: Dict = {}
        names = {f.name(): f for f in grid_src.fields()}
        for fld in ("row", "col", "active"):
            if fld not in names:
                rep.error("grid-field", f"grid layer has no field '{fld}'")
        if authid and all(n in names for n in ("row", "col")):
            try:
                spec, active = common.grid_spec_from_source(grid_src, authid, feedback)
                rep.info("grid", f"grid layer is a complete uniform grid: {spec.nrow} rows x {spec.ncol} cols, "
                                 f"cell {spec.delr:g} x {spec.delc:g} m, {sum(1 for a in active.values() if a)} active cells")
                if "active" in names and any(a not in (0, 1) for a in active.values()):
                    rep.error("grid-active", "field 'active' holds values other than 0 and 1")
            except QgsProcessingException as exc:
                rep.error("grid-layer", str(exc))

        # 3. grid.json -----------------------------------------------------------
        if grid_json:
            try:
                js = gridmod.read_grid_json(grid_json)
                if authid and js.crs != authid:
                    rep.error("grid-json-crs", f"grid.json CRS {js.crs} differs from the layer CRS {authid}")
                if spec is not None:
                    tol = 1e-6 * max(spec.delr, spec.delc)
                    diffs = [n for n in ("xorigin", "yorigin", "delr", "delc") if abs(getattr(js, n) - getattr(spec, n)) > tol]
                    diffs += [n for n in ("nrow", "ncol") if getattr(js, n) != getattr(spec, n)]
                    if diffs:
                        rep.error("grid-json-mismatch", "grid.json disagrees with the grid layer in: " + ", ".join(diffs))
                    else:
                        rep.info("grid-json", "grid.json agrees with the grid layer")
            except (OSError, ValueError) as exc:
                rep.error("grid-json", f"grid.json cannot be used: {exc}")

        # 4. domain coverage -----------------------------------------------------
        if domain is not None and spec is not None:
            self._check_domain(rep, domain, spec, active)

        # 5. HRUs ----------------------------------------------------------------
        hru_areas: Optional[Dict[int, float]] = None
        if hrus is not None:
            hru_areas = self._layer_ids(rep, hrus, hru_field, "HRU", "hru", polygon=True, spec=spec)
        chan_lengths: Optional[Dict[int, float]] = None
        if chans is not None:
            chan_lengths = self._layer_ids(rep, chans, chan_field, "channel", "chan", polygon=False, spec=spec)

        # 6. tables --------------------------------------------------------------
        if hru_csv:
            try:
                rep.extend(validation.check_hru_table(contract.read_hru_cells(hru_csv), spec, hru_areas))
            except (OSError, contract.ContractError) as exc:
                rep.error("hru-table", f"hru_cells.csv cannot be read: {exc}")
        if chan_csv:
            try:
                rep.extend(validation.check_channel_table(contract.read_channel_cells(chan_csv), spec, chan_lengths))
            except (OSError, contract.ContractError) as exc:
                rep.error("chan-table", f"channel_cells.csv cannot be read: {exc}")

        # report --------------------------------------------------------------------
        text = rep.to_text()
        for line in text.splitlines():
            feedback.pushInfo(line)
        for issue in rep.errors:
            feedback.reportError(f"[{issue.code}] {issue.message}", False)
        report_path = self.parameterAsFileOutput(parameters, self.REPORT, context) if self.REPORT in parameters and parameters[self.REPORT] else None
        results = {"ERRORS": len(rep.errors), "WARNINGS": len(rep.warnings), "REPORT_TEXT": text}
        if report_path:
            with open(report_path, "w", encoding="utf-8") as fh:
                fh.write(rep.to_html())
            results[self.REPORT] = report_path
        return results

    # ------------------------------------------------------------------
    def _check_domain(self, rep, domain, spec, active):
        geoms = [f.geometry() for f in domain.getFeatures() if not f.geometry().isNull() and not f.geometry().isEmpty()]
        if not geoms:
            rep.error("domain-empty", "domain layer has no geometry")
            return
        dom = QgsGeometry.unaryUnion([g if g.isGeosValid() else g.makeValid() for g in geoms])
        grid_rect = QgsGeometry.fromRect(QgsRectangle(*spec.extent))
        outside = dom.difference(grid_rect).area()
        total = dom.area()
        if total <= 0:
            rep.error("domain-area", "domain has zero area")
            return
        if outside > 1e-9 * total:
            rep.error("domain-not-covered", f"the grid does not cover the domain: {outside:.1f} m2 ({100 * outside / total:.3f}%) lies outside")
        else:
            rep.info("domain-covered", "the grid covers the whole domain")
        engine = QgsGeometry.createGeometryEngine(dom.constGet())
        engine.prepareGeometry()
        bb = dom.boundingBox()
        rng = spec.cell_range(bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum())
        should = set()
        if rng:
            r0, r1, c0, c1 = rng
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    rect = QgsGeometry.fromRect(QgsRectangle(*spec.cell_bounds(r, c)))  # keep the owner alive
                    if engine.intersects(rect.constGet()) and not engine.touches(rect.constGet()):
                        should.add((r, c))
        have = {rc for rc, a in active.items() if a}
        missing, extra = sorted(should - have), sorted(have - should)
        if missing:
            rep.warning("active-missing", f"{len(missing)} cell(s) overlap the domain but are inactive, e.g. {missing[:5]}")
        if extra:
            rep.warning("active-extra", f"{len(extra)} active cell(s) do not overlap the domain, e.g. {extra[:5]}")
        if not missing and not extra:
            rep.info("active", "the 'active' flag matches the domain overlap")

    def _layer_ids(self, rep, src, field, label, code, polygon, spec):
        """Check id field; returns {id: polygon area or line length}."""
        if field not in {f.name() for f in src.fields()}:
            rep.error(f"{code}-id-field", f"{label} layer has no field '{field}'")
            return None
        values: Dict[int, float] = defaultdict(float)
        n_feat = n_null = 0
        bad: List[str] = []
        outside = 0
        grid_rect = QgsGeometry.fromRect(QgsRectangle(*spec.extent)) if spec is not None else None
        for f in src.getFeatures():
            n_feat += 1
            try:
                fid = common.as_int_id(f[field], f"{label} layer", f.id())
            except QgsProcessingException as exc:
                bad.append(str(exc))
                continue
            g = f.geometry()
            if g.isNull() or g.isEmpty():
                rep.warning(f"{code}-geometry", f"{label} {fid} (feature {f.id()}) has no geometry")
                continue
            values[fid] += g.area() if polygon else g.length()
            if grid_rect is not None and not grid_rect.contains(g):
                outside += 1
        if bad:
            rep.error(f"{code}-id", f"{len(bad)} {label} feature(s) without a usable integer id; first: {bad[0]}")
        if n_feat == 0:
            rep.error(f"{code}-empty", f"{label} layer has no features")
        else:
            rep.info(f"{code}-ids", f"{label} layer: {n_feat} feature(s), {len(values)} distinct id(s)")
        if len(values) < n_feat - len(bad):
            rep.info(f"{code}-multipart", f"{n_feat - len(bad) - len(values)} extra feature(s) share an id with another; they are combined")
        if outside:
            rep.warning(f"{code}-outside", f"{outside} {label} feature(s) are not completely inside the grid")
        return dict(values)
