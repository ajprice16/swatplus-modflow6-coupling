"""Processing algorithm tests (run headless inside QGIS; see run_qgis_tests.sh)."""
import csv
import math
import tempfile
import unittest
from pathlib import Path

import qgis_env as E  # noqa: E402  (importing starts nothing yet)

E.start_qgis()
E.register_provider()

from qgis.core import QgsProcessingException, QgsVectorLayer  # noqa: E402

from swatplus_modflow_qgis.core import contract, grid as gridmod  # noqa: E402


def _rows(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


class AlgorithmTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="swatplus_qgis_", ignore_cleanup_errors=True)
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class TestCreateGrid(AlgorithmTestCase):
    def test_orientation_fields_and_json(self):
        lyr, js = E.grid_layer_from_alg(self.dir)
        spec = gridmod.read_grid_json(js)
        self.assertEqual((spec.nrow, spec.ncol, spec.delr, spec.delc), (E.NROW, E.NCOL, E.D, E.D))
        self.assertEqual((spec.xorigin, spec.yorigin, spec.angrot), (E.X0, E.Y0, 0.0))
        self.assertEqual(spec.crs, E.CRS)
        self.assertEqual([f.name() for f in lyr.fields()][-3:], ["row", "col", "active"])
        self.assertEqual(lyr.featureCount(), E.NROW * E.NCOL)
        for f in lyr.getFeatures():
            bb = f.geometry().boundingBox()
            self.assertEqual((bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum()), spec.cell_bounds(f["row"], f["col"]))
            self.assertEqual(f["active"], 1)
        nw = next(f for f in lyr.getFeatures() if f["row"] == 1 and f["col"] == 1)
        self.assertEqual(nw.geometry().boundingBox().yMaximum(), spec.ytop)   # row 1 is north
        self.assertEqual(nw.geometry().boundingBox().xMinimum(), spec.xorigin)  # col 1 is west

    def test_snap_buffer_and_active_flags(self):
        lyr, js = E.grid_layer_from_alg(self.dir, domain=E.domain_layer(), size=100.0, buffer_=50.0)
        spec = gridmod.read_grid_json(js)
        self.assertEqual((spec.xorigin, spec.yorigin), (900.0, 1900.0))
        self.assertGreaterEqual(spec.xmax, 1388 + 50)
        self.assertGreaterEqual(spec.ytop, 2290 + 50)
        act = {(f["row"], f["col"]): f["active"] for f in lyr.getFeatures()}
        # the domain is L-shaped: the bbox corner east of x=1250 and north of y=2150 is cut out
        self.assertEqual(act[(spec.nrow, spec.ncol)], 0)    # buffer-ring corner
        r, c = spec.locate(1100.0, 2100.0)
        self.assertEqual(act[(r, c)], 1)
        r, c = spec.locate(1350.0, 2050.0)                  # south-east arm of the L: inside
        self.assertEqual(act[(r, c)], 1)
        r, c = spec.locate(1350.0, 2250.0)                  # cut-out corner
        self.assertEqual(act[(r, c)], 0)

    def test_touching_cells_are_inactive(self):
        lyr, _ = E.grid_layer_from_alg(self.dir, domain=E.rect_domain(), size=100.0, buffer_=100.0)
        act = {(f["row"], f["col"]): f["active"] for f in lyr.getFeatures()}
        self.assertEqual(sum(act.values()), E.NROW * E.NCOL)   # buffer ring cells only touch the domain edge
        self.assertEqual(len(act), (E.NROW + 2) * (E.NCOL + 2))
        self.assertEqual(act[(1, 1)], 0)

    def test_rejects_geographic_crs(self):
        dom = E.rect_domain(E.GEO_CRS)
        with self.assertRaises(QgsProcessingException):
            E.run_alg("swatplusmodflow:creategrid", {"DOMAIN": dom, "CELL_SIZE": 0.01, "BUFFER": 0.0, "SNAP": True,
                                                    "OUTPUT_GRID": str(self.dir / "g.gpkg"), "OUTPUT_JSON": str(self.dir / "g.json")})

    def test_rejects_missing_cell_size(self):
        with self.assertRaises(Exception):
            E.run_alg("swatplusmodflow:creategrid", {"DOMAIN": E.rect_domain(), "BUFFER": 0.0,
                                                    "OUTPUT_GRID": str(self.dir / "g.gpkg"), "OUTPUT_JSON": str(self.dir / "g.json")})


class TestHruConnections(AlgorithmTestCase):
    def run_alg(self, hrus=None, grid=None, **kw):
        grid = grid or E.grid_layer_from_alg(self.dir)[0]
        out = self.dir / "hru_cells.csv"
        params = {"HRUS": hrus or E.hru_layer(), "ID_FIELD": "HRUS", "GRID": grid, "LAYER": 1, "OUTPUT": str(out)}
        params.update(kw)
        res = E.run_alg("swatplusmodflow:hruconnections", params)
        return res, contract.read_hru_cells(out)

    def table(self, rows):
        out = {}
        for r in rows:
            out.setdefault(r["hru_id"], {})[(r["row"], r["col"])] = r
        return out

    def test_areas_fractions_and_orientation(self):
        res, rows = self.run_alg()
        t = self.table(rows)
        # HRU 1: top row (row 1 = north), columns 1 and 2
        self.assertEqual(set(t[1]), {(1, 1), (1, 2)})
        for r in t[1].values():
            self.assertAlmostEqual(r["fraction"], 0.5)
            self.assertAlmostEqual(r["area_m2"], 10000.0)
        # HRU 2: triangle completely inside cell (2, 3)
        self.assertEqual(set(t[2]), {(2, 3)})
        self.assertAlmostEqual(t[2][(2, 3)]["area_m2"], 5000.0)
        self.assertAlmostEqual(t[2][(2, 3)]["fraction"], 1.0)
        # HRU 3: 1/3 of its area is west of the grid -> fraction 2/3, not rescaled
        self.assertEqual(set(t[3]), {(3, 1)})
        self.assertAlmostEqual(t[3][(3, 1)]["fraction"], 10000.0 / 15000.0)
        # HRU 4 is outside: no rows; HRU 5 has two polygons combined
        self.assertNotIn(4, t)
        self.assertEqual(set(t[5]), {(4, 1), (4, 5)})
        self.assertAlmostEqual(sum(r["fraction"] for r in t[5].values()), 1.0)
        # counts reported
        self.assertEqual((res["HRU_COUNT"], res["PARTIAL_HRUS"], res["UNCOVERED_HRUS"]), (5, 1, 1))
        for r in rows:
            self.assertEqual(r["layer"], 1)
            self.assertTrue(1 <= r["row"] <= E.NROW and 1 <= r["col"] <= E.NCOL)

    def test_fractions_sum_to_one_inside_grid_and_mass_is_conserved(self):
        _, rows = self.run_alg()
        sums = {}
        for r in rows:
            sums[r["hru_id"]] = sums.get(r["hru_id"], 0.0) + r["fraction"]
        for hid in (1, 2, 5):
            self.assertAlmostEqual(sums[hid], 1.0, places=9)
        self.assertLess(sums[3], 1.0)

    def test_string_ids_and_layer_number(self):
        _, rows = self.run_alg(hrus=E.hru_layer("str"), LAYER=2)
        self.assertEqual({r["hru_id"] for r in rows}, {1, 2, 3, 5})
        self.assertTrue(all(r["layer"] == 2 for r in rows))

    def test_crs_mismatch_is_an_error(self):
        other = E.hru_layer(crs="EPSG:32632")
        with self.assertRaises(QgsProcessingException):
            self.run_alg(hrus=other)

    def test_missing_id_field_is_an_error(self):
        with self.assertRaises(QgsProcessingException):
            self.run_alg(ID_FIELD="NoSuchField")


class TestChannelConnections(AlgorithmTestCase):
    def run_alg(self, **kw):
        grid = E.grid_layer_from_alg(self.dir)[0]
        out = self.dir / "channel_cells.csv"
        params = {"CHANNELS": E.channel_layer(), "ID_FIELD": "Channel", "GRID": grid, "LAYER": 1,
                  "REVERSE": False, "TOLERANCE": 0.1, "OUTPUT": str(out)}
        params.update(kw)
        res = E.run_alg("swatplusmodflow:channelconnections", params)
        by = {}
        for r in contract.read_channel_cells(out):
            by.setdefault(r["channel_id"], []).append(r)
        return res, by

    def test_simple_channel_seq_length_fraction(self):
        _, by = self.run_alg()
        c1 = by[1]
        self.assertEqual([(r["seq"], r["row"], r["col"]) for r in c1], [(1, 2, 1), (2, 2, 2)])
        self.assertAlmostEqual(c1[0]["length_m"], 90.0)
        self.assertAlmostEqual(sum(r["fraction"] for r in c1), 1.0)

    def test_seq_follows_digitising_direction_and_reverse_flips_it(self):
        _, by = self.run_alg()
        _, rev = self.run_alg(REVERSE=True)
        self.assertEqual([(r["row"], r["col"]) for r in rev[1]], [(2, 2), (2, 1)])
        self.assertEqual([r["seq"] for r in rev[1]], [1, 2])
        self.assertAlmostEqual(rev[1][0]["length_m"], 90.0)

    def test_multipart_chain_merges_boundary_piece(self):
        _, by = self.run_alg()
        c2 = by[2]
        # parts are chained from the free end of the first part: south end first, running north
        self.assertEqual([(r["row"], r["col"]) for r in c2], [(3, 2), (2, 2), (1, 2)])
        self.assertEqual([r["seq"] for r in c2], [1, 2, 3])
        self.assertAlmostEqual(sum(r["length_m"] for r in c2), 250.0)
        self.assertAlmostEqual(sum(r["fraction"] for r in c2), 1.0)
        self.assertAlmostEqual(c2[1]["length_m"], 100.0)       # 50 m from each part merged into row 2

    def test_dem_orients_downhill(self):
        dem = E.write_dem(self.dir / "dem.asc")
        _, by = self.run_alg(DEM=dem)
        # the DEM falls to the south: channel 2 must run north -> south, channel 1 (flat) keeps its direction
        self.assertEqual([(r["row"], r["col"]) for r in by[2]], [(1, 2), (2, 2), (3, 2)])
        self.assertEqual([(r["row"], r["col"]) for r in by[1]], [(2, 1), (2, 2)])
        res, _ = self.run_alg(DEM=dem)
        self.assertEqual(res["FLIPPED_BY_DEM"], 1)

    def test_partial_and_uncovered_channels(self):
        res, by = self.run_alg()
        self.assertEqual([(r["row"], r["col"]) for r in by[3]], [(4, 5)])
        self.assertAlmostEqual(by[3][0]["length_m"], 100.0)
        self.assertAlmostEqual(by[3][0]["fraction"], 100.0 / 250.0)   # total length includes the part outside
        self.assertNotIn(4, by)
        self.assertEqual((res["CHANNEL_COUNT"], res["PARTIAL_CHANNELS"], res["UNCOVERED_CHANNELS"]), (4, 1, 1))

    def test_crs_mismatch_is_an_error(self):
        with self.assertRaises(QgsProcessingException):
            self.run_alg(CHANNELS=E.channel_layer(crs="EPSG:32632"))


class TestValidateInputs(AlgorithmTestCase):
    def prepare(self):
        grid, js = E.grid_layer_from_alg(self.dir)
        hru_csv, ch_csv = self.dir / "hru_cells.csv", self.dir / "channel_cells.csv"
        hrus, chans = E.hru_layer(), E.channel_layer()
        E.run_alg("swatplusmodflow:hruconnections", {"HRUS": hrus, "ID_FIELD": "HRUS", "GRID": grid, "LAYER": 1, "OUTPUT": str(hru_csv)})
        E.run_alg("swatplusmodflow:channelconnections", {"CHANNELS": chans, "ID_FIELD": "Channel", "GRID": grid, "LAYER": 1,
                                                         "REVERSE": False, "TOLERANCE": 0.1, "OUTPUT": str(ch_csv)})
        return dict(GRID=grid, GRID_JSON=js, DOMAIN=E.rect_domain(), HRUS=hrus, HRU_ID_FIELD="HRUS", CHANNELS=chans,
                    CHANNEL_ID_FIELD="Channel", HRU_CELLS=str(hru_csv), CHANNEL_CELLS=str(ch_csv)), hru_csv

    def test_clean_inputs_have_no_errors_but_report_partial_coverage(self):
        params, _ = self.prepare()
        params["REPORT"] = str(self.dir / "report.html")
        res = E.run_alg("swatplusmodflow:validateinputs", params)
        self.assertEqual(res["ERRORS"], 0, res["REPORT_TEXT"])
        self.assertIn("hru-partial", res["REPORT_TEXT"])
        self.assertIn("chan-partial", res["REPORT_TEXT"])
        self.assertIn("hru-uncovered", res["REPORT_TEXT"])
        self.assertTrue((self.dir / "report.html").read_text().startswith("<html>"))

    def test_detects_crs_mismatch_and_table_damage(self):
        params, hru_csv = self.prepare()
        params["HRUS"] = E.hru_layer(crs="EPSG:32632")
        res = E.run_alg("swatplusmodflow:validateinputs", params)
        self.assertIn("crs-mismatch", res["REPORT_TEXT"])
        params, hru_csv = self.prepare()
        text = hru_csv.read_text().replace(",1,1,2,", ",1,9,2,", 1)    # push one cell outside the grid
        hru_csv.write_text(text)
        res = E.run_alg("swatplusmodflow:validateinputs", params)
        self.assertGreater(res["ERRORS"], 0)
        self.assertIn("hru-outside-grid", res["REPORT_TEXT"])

    def test_detects_uncovered_domain_and_geographic_crs(self):
        params, _ = self.prepare()
        big = E.mem_layer("Polygon", E.CRS, [("n", E.QMetaType.Type.Int)], "bigdomain")
        E.add_feature(big, E.rect_wkt(E.X0 - 200, E.Y0, E.X0 + 600, E.Y0 + 400), [1])
        params["DOMAIN"] = big
        res = E.run_alg("swatplusmodflow:validateinputs", params)
        self.assertIn("domain-not-covered", res["REPORT_TEXT"])
        params["DOMAIN"] = E.rect_domain(E.GEO_CRS)
        res = E.run_alg("swatplusmodflow:validateinputs", params)
        self.assertIn("crs-geographic", res["REPORT_TEXT"])


if __name__ == "__main__":
    unittest.main()
