import tempfile
import os
import unittest
from pathlib import Path

from swatplus_modflow.interfaces.swatplus_interface import SWATPlusInterface


class SWATPlusInterfaceTest(unittest.TestCase):
    def test_reads_hru_area_from_qswat_connection_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp)
            (model_dir / "file.cio").write_text("placeholder\n")
            (model_dir / "hru.con").write_text(
                "hru.con: test file\n"
                "id name gis_id area lat lon elev hru wst cst ovfl rule out_tot\n"
                "1 hru01 1 718.63250 31.75482 -110.47206 1620.71826 1 sta 0 0 0 0\n"
            )
            (model_dir / "hru-data.hru").write_text(
                "hru-data.hru: test file\n"
                "id name topo hydro soil lu_mgt soil_plant_init surf_stor snow field\n"
                "1 hru01 topohru01 hyd01 53902 rngb_lum soilplant1 null snow001 null\n"
            )

            interface = SWATPlusInterface(model_dir, executable="/bin/true")
            info = interface.get_object_info()

            self.assertEqual(info["n_hrus"], 1)
            hru = info["hru_properties"][1]
            self.assertAlmostEqual(hru["area_ha"], 718.63250)
            self.assertAlmostEqual(hru["area_km2"], 7.186325)
            self.assertEqual(hru["soil_id"], 53902)
            self.assertAlmostEqual(hru["lat"], 31.75482)
            self.assertAlmostEqual(hru["lon"], -110.47206)

    def test_skips_output_files_from_previous_swatplus_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp)
            (model_dir / "file.cio").write_text("placeholder\n")
            marker = model_dir / "print.prt"
            marker.write_text("current run marker\n")
            stale = model_dir / "channel_sdmorph_day.csv"
            stale.write_text("old output\n")

            now = 1_700_000_000
            os.utime(marker, (now, now))
            os.utime(stale, (now - 3600, now - 3600))

            interface = SWATPlusInterface(model_dir, executable="/bin/true")

            self.assertFalse(
                interface._is_current_output_file(stale, interface._current_run_mtime())
            )


if __name__ == "__main__":
    unittest.main()
