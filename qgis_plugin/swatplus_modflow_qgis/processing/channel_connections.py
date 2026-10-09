"""Build channel connections (channel_cells.csv)."""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from typing import Dict, List, Optional, Tuple

from qgis.core import (Qgis, QgsCoordinateTransform, QgsPointXY, QgsProcessingAlgorithm, QgsProcessingException,
                       QgsProcessingOutputNumber, QgsProcessingParameterBoolean, QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterField, QgsProcessingParameterFileDestination,
                       QgsProcessingParameterNumber, QgsProcessingParameterRasterLayer)

from ..core import channel_order, contract
from ..core import grid as gridmod
from . import common

_MAX_LISTED = 40


class ChannelConnectionsAlgorithm(QgsProcessingAlgorithm):
    CHANNELS = "CHANNELS"
    ID_FIELD = "ID_FIELD"
    GRID = "GRID"
    LAYER = "LAYER"
    REVERSE = "REVERSE"
    DEM = "DEM"
    TOLERANCE = "TOLERANCE"
    OUTPUT = "OUTPUT"

    def name(self): return "channelconnections"
    def displayName(self): return "Build channel connections"
    def group(self): return common.GROUP_NAME
    def groupId(self): return common.GROUP_ID
    def createInstance(self): return ChannelConnectionsAlgorithm()

    def shortHelpString(self):
        return ("Cuts channel lines at the MODFLOW cell boundaries and writes channel_cells.csv "
                "(channel_id, seq, layer, row, col, length_m, fraction).\n\n"
                "seq numbers the pieces of a channel from its UPSTREAM end (1, 2, ...); a channel that leaves a "
                "cell and comes back gets a new piece. length_m is the planar length inside the cell and fraction "
                "= length_m / total channel length (all parts, also the part outside the grid), so fractions sum "
                "to less than 1 for a channel that leaves the grid; this is reported in the log.\n\n"
                "Direction assumption: each channel is digitised upstream to downstream (as in the stream layer "
                "produced by QSWAT+, 'rivs1'). Tick 'Reverse' if your layer is digitised the other way. "
                "Optionally give a DEM: the elevation at both ends of every channel is compared and the channel is "
                "oriented downhill, overriding both the digitising direction and 'Reverse'; channels the DEM cannot "
                "decide (equal elevations or no data) fall back to the digitising direction (and 'Reverse'). The log "
                "reports how many channels the DEM flipped.\n\n"
                "Multipart features are chained end to end (flipping parts as needed); joins wider than the "
                "tolerance are reported. The id field defaults to 'Channel' as in QSWAT+ 'rivs1'.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.CHANNELS, "Channel line layer", [Qgis.ProcessingSourceType.VectorLine]))
        self.addParameter(QgsProcessingParameterField(
            self.ID_FIELD, "Channel id field", "Channel", self.CHANNELS, Qgis.ProcessingFieldParameterDataType.Any))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.GRID, "MODFLOW grid layer (fields row, col, active)", [Qgis.ProcessingSourceType.VectorPolygon]))
        self.addParameter(QgsProcessingParameterNumber(
            self.LAYER, "MODFLOW layer of the channel cells", Qgis.ProcessingNumberParameterType.Integer, 1, minValue=1))
        self.addParameter(QgsProcessingParameterBoolean(
            self.REVERSE, "Reverse: lines are digitised downstream to upstream", False))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, "DEM to orient channels downhill (optional)", optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.TOLERANCE, "Tolerance for joining line parts (map units)", Qgis.ProcessingNumberParameterType.Double,
            0.1, minValue=0.0))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT, "channel_cells.csv", "CSV files (*.csv)"))
        for key, label in (("CHANNEL_COUNT", "Channels in layer"), ("PARTIAL_CHANNELS", "Channels partly outside the grid"),
                           ("UNCOVERED_CHANNELS", "Channels entirely outside the grid"),
                           ("FLIPPED_BY_DEM", "Channels reversed by the DEM"), ("ROWS", "Rows written")):
            self.addOutput(QgsProcessingOutputNumber(key, label))

    # ------------------------------------------------------------------
    def _sampler(self, dem, crs, context):
        if dem is None:
            return None
        xform = None
        if dem.crs() != crs:
            xform = QgsCoordinateTransform(crs, dem.crs(), context.transformContext())
        provider = dem.dataProvider()

        def sample(pt):
            p = QgsPointXY(pt[0], pt[1])
            if xform is not None:
                p = xform.transform(p)
            value, ok = provider.sample(p, 1)
            return float(value) if ok else None
        return sample

    def processAlgorithm(self, parameters, context, feedback):
        chans = self.parameterAsSource(parameters, self.CHANNELS, context)
        grid_src = self.parameterAsSource(parameters, self.GRID, context)
        if chans is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.CHANNELS))
        if grid_src is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.GRID))
        id_field = self.parameterAsString(parameters, self.ID_FIELD, context)
        layer_no = self.parameterAsInt(parameters, self.LAYER, context)
        reverse = self.parameterAsBool(parameters, self.REVERSE, context)
        tol = self.parameterAsDouble(parameters, self.TOLERANCE, context)
        dem = self.parameterAsRasterLayer(parameters, self.DEM, context)
        out_path = self.parameterAsFileOutput(parameters, self.OUTPUT, context)

        authid = common.authid_of(grid_src.sourceCrs(), "Grid layer")
        common.require_same_crs(authid, "the grid", chans, "Channel layer")
        common.require_fields(chans, [id_field], "Channel layer")
        spec, _active = common.grid_spec_from_source(grid_src, authid, feedback)
        sample = self._sampler(dem, grid_src.sourceCrs(), context)
        feedback.pushInfo(f"Grid {spec.nrow} x {spec.ncol}, CRS {authid}; orientation: "
                          + ("DEM elevation check" if sample else ("reversed digitising direction" if reverse else "digitising direction (upstream to downstream)")))

        parts_by_id: "OrderedDict[int, List[List[Tuple[float, float]]]]" = OrderedDict()
        for f in chans.getFeatures():
            cid = common.as_int_id(f[id_field], "Channel layer", f.id())
            parts_by_id.setdefault(cid, []).extend(common.parts_of(f.geometry()))

        rows = []
        flipped_dem = undecided = gap_channels = 0
        empty, uncovered, partial = [], [], []
        covered: Dict[int, float] = {}
        total = len(parts_by_id) or 1
        for i, (cid, parts) in enumerate(parts_by_id.items()):
            if feedback.isCanceled():
                break
            feedback.setProgress(100.0 * i / total)
            parts = [p for p in parts if gridmod.polyline_length(p) > 0]
            if not parts:
                empty.append(cid)
                continue
            chain, gaps = channel_order.chain_parts(parts, tol)
            if gaps:
                gap_channels += 1
            flip = reverse
            if sample is not None:
                ends = channel_order.end_points(chain)
                downhill = channel_order.decide_downhill(sample(ends[0]), sample(ends[1]))
                if downhill is None:
                    undecided += 1
                else:
                    flip = not downhill
                    flipped_dem += int(flip)
            chain = channel_order.orient(chain, flip)

            total_len = sum(gridmod.polyline_length(p) for p in chain)
            pieces: List[gridmod.Piece] = []
            for part in chain:
                for pc in gridmod.split_polyline(part, spec):
                    if pieces and (pieces[-1].row, pieces[-1].col) == (pc.row, pc.col):
                        pieces[-1].length += pc.length
                    else:
                        pieces.append(gridmod.Piece(pc.row, pc.col, pc.length))
            inside = [p for p in pieces if p.row is not None]
            cov = sum(p.length for p in inside) / total_len
            covered[cid] = cov
            if not inside:
                uncovered.append(cid)
                continue
            if cov < 1.0 - 1e-6:
                partial.append(cid)
            for seq, p in enumerate(inside, start=1):
                rows.append({"channel_id": cid, "seq": seq, "layer": layer_no, "row": p.row, "col": p.col,
                             "length_m": p.length, "fraction": p.length / total_len})
        n = contract.write_channel_cells(out_path, rows)

        feedback.pushInfo(f"{len(parts_by_id)} channels, {n} channel-cell rows written to {out_path}")
        if sample is not None:
            feedback.pushInfo(f"DEM check: {flipped_dem} channel(s) reversed, {undecided} undecided (used the digitising direction"
                              + (" reversed" if reverse else "") + ").")
        if gap_channels:
            feedback.pushWarning(f"{gap_channels} multipart channel(s) have parts that do not join within {tol:g} map units; "
                                 "the gaps are not counted in the channel length.")
        if empty:
            feedback.reportError(f"{len(empty)} channel(s) have no usable line geometry and are skipped: "
                                 + ", ".join(map(str, empty[:_MAX_LISTED])), False)
        if uncovered:
            feedback.reportError(f"{len(uncovered)} channel(s) lie entirely outside the grid and have no rows: "
                                 + ", ".join(map(str, uncovered[:_MAX_LISTED])), False)
        if partial:
            worst = sorted(partial, key=lambda c: covered[c])[:_MAX_LISTED]
            feedback.reportError(f"{len(partial)} channel(s) leave the grid (fractions sum to < 1): "
                                 + ", ".join(f"{c} ({covered[c]:.4f})" for c in worst), False)
        return {self.OUTPUT: out_path, "CHANNEL_COUNT": len(parts_by_id), "PARTIAL_CHANNELS": len(partial),
                "UNCOVERED_CHANNELS": len(uncovered), "FLIPPED_BY_DEM": flipped_dem, "ROWS": n}
