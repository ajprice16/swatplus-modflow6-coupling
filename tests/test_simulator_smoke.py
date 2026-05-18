"""Smoke tests for the coupled simulator orchestration."""

from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from swatplus_modflow.core.simulator import CouplingSettings, SWATPlusMODFLOWCoupler


class FakeSWATPlusInterface:
    def __init__(self, model_dir, executable=None):
        self.model_dir = Path(model_dir)
        self.feedback = []

    def get_object_info(self):
        return {
            "hru_properties": {
                1: {"area_km2": 1.0},
            },
        }

    def initialize(self, start_date, end_date):
        self.start_date = start_date
        self.end_date = end_date

    def advance_one_day(self, current_date):
        return {
            "recharge": {1: 1.0},
            "unsatisfied_et": {1: 0.2},
            "channel_stage": {7: 100.0},
            "irrigation_demand": {1: 0.1},
            "soil_water": {1: 50.0},
        }

    def update_groundwater_state(self, baseflow, soil_saturation, drain_flow):
        self.feedback.append(
            {
                "baseflow": baseflow,
                "soil_saturation": soil_saturation,
                "drain_flow": drain_flow,
            }
        )


class FakeMODFLOWInterface:
    def __init__(self, model_dir, executable=None):
        self.model_dir = Path(model_dir)
        self.calls = []

    def get_grid_info(self):
        return {"nrow": 1, "ncol": 1, "nlayers": 1, "dx": 100.0, "dy": 100.0}

    def initialize(self, start_date, end_date, convergence_tol=1e-4, max_iterations=10000):
        self.start_date = start_date
        self.end_date = end_date

    def get_initial_heads(self):
        return {0: 99.5}

    def advance_one_day(self, recharge_cells, et_cells, pumping_cells, channel_stages, current_date):
        channel_exchange = {
            0: sum(recharge_cells.values()) - sum(et_cells.values()) + sum(pumping_cells.values())
        }
        self.calls.append(
            {
                "date": current_date,
                "recharge": recharge_cells,
                "et": et_cells,
                "pumping": pumping_cells,
                "channel_stages": channel_stages,
            }
        )
        return {
            "heads": {0: 99.4},
            "channel_exchange": channel_exchange,
            "drain_exchange": {},
            "budget": {},
        }


class FakeGeographicMapper:
    def __init__(self, connections_file, modflow_grid, swatplus_objects):
        self.connections_file = Path(connections_file)
        self.modflow_grid = modflow_grid
        self.swatplus_objects = swatplus_objects
        self.hru_to_cells = {1: [(0, 1.0)]}
        self.channel_to_cells = {7: [(0, 100.0)]}
        self.reservoir_to_cells = {}
        self.canal_to_cells = {}
        self.drain_to_cells = {}
        self.n_hru_connections = 1
        self.n_channel_connections = 1
        self.n_drain_connections = 0

    def load_connections(self):
        return None


class TestSimulatorSmoke(unittest.TestCase):
    def test_two_day_coupled_run_with_fake_interfaces(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = CouplingSettings(
                modflow_dir=Path(tmpdir) / "mf6",
                swatplus_dir=Path(tmpdir) / "swat",
                geo_connections_file=Path(tmpdir) / "connections.gpkg",
                output_dir=Path(tmpdir) / "out",
                start_date=datetime(2020, 1, 1),
                end_date=datetime(2020, 1, 2),
                recharge_delay_days=1.0,
            )

            with patch("swatplus_modflow.core.simulator.SWATPlusInterface", FakeSWATPlusInterface), patch(
                "swatplus_modflow.core.simulator.MODFLOWInterface", FakeMODFLOWInterface
            ), patch("swatplus_modflow.core.simulator.GeographicMapper", FakeGeographicMapper):
                coupler = SWATPlusMODFLOWCoupler(settings)
                coupler.load_geographic_connections()
                result = coupler.run()

            daily = coupler.get_daily_flows()
            self.assertEqual(len(daily), 2)
            self.assertEqual(len(coupler.modflow.calls), 2)
            self.assertEqual(coupler.modflow.calls[0]["recharge"], {0: 500.0})
            self.assertEqual(coupler.modflow.calls[0]["pumping"], {0: -100.0})
            self.assertIn("water_budget", result)
            self.assertTrue((settings.output_dir / "daily_flows_summary.csv").exists())
            self.assertEqual(coupler.swatplus.feedback[0]["baseflow"], {7: 200.0})


if __name__ == "__main__":
    unittest.main()
