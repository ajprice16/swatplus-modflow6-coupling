"""Tests for geographic connection loading."""

import tempfile
import unittest
from pathlib import Path

from swatplus_modflow.coupling.geographic_mapper import GeographicMapper


class TestGeographicMapper(unittest.TestCase):
    def test_loads_verde_hru_connection_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hru_cell_connections.csv"
            path.write_text(
                "hru_id,subbasin,mf_row,mf_col,overlap_area_m2,frac_of_hru,frac_of_cell\n"
                "10,1,0,1,600000,0.6,0.6\n"
                "10,1,1,1,400000,0.4,0.4\n"
            )

            mapper = GeographicMapper(
                connections_file=path,
                modflow_grid={"nrow": 2, "ncol": 3},
                swatplus_objects={},
            )
            mapper.load_connections()

            self.assertEqual(mapper.n_hru_connections, 2)
            self.assertEqual(mapper.hru_to_cells[10], [(1, 0.6), (4, 0.4)])

    def test_loads_verde_riv_channel_connection_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "riv_channel_connections.csv"
            path.write_text(
                "mf_row,mf_col,channel_id,chan_length_m,snap_dist_m\n"
                "0,2,7,900.0,20.0\n"
                "1,0,7,1100.0,30.0\n"
            )

            mapper = GeographicMapper(
                connections_file=path,
                modflow_grid={"nrow": 2, "ncol": 3},
                swatplus_objects={},
            )
            mapper.load_connections()

            self.assertEqual(mapper.n_channel_connections, 2)
            self.assertEqual(mapper.channel_to_cells[7], [(2, 900.0), (3, 1100.0)])

    def test_generic_csv_can_use_one_based_row_col_indices(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generic_connections.csv"
            path.write_text(
                "swat_obj_type,swat_obj_id,mf_layer,mf_row,mf_col,fraction\n"
                "HRU,3,1,1,1,1.0\n"
            )

            mapper = GeographicMapper(
                connections_file=path,
                modflow_grid={"nrow": 2, "ncol": 3, "index_base": 1},
                swatplus_objects={},
            )
            mapper.load_connections()

            self.assertEqual(mapper.hru_to_cells[3], [(0, 1.0)])


if __name__ == "__main__":
    unittest.main()
