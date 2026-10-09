"""Tests for MODFLOW interface boundary-condition updates."""

import unittest

import numpy as np

from swatplus_modflow.interfaces.modflow_interface import MODFLOWInterface


class _ArrayData:
    def __init__(self):
        self.calls = []

    def set_data(self, data, key=0):
        self.calls.append((key, data))


class _StressPeriodData:
    def __init__(self, existing=None):
        self.existing = existing
        self.calls = []

    def get_data(self, key=0):
        return self.existing

    def set_data(self, data, key=0):
        self.calls.append((key, data))


class _FakeModel:
    def __init__(self):
        self.rch = type("FakeRCH", (), {"recharge": _ArrayData()})()
        self.evt = type("FakeEVT", (), {"stress_period_data": _StressPeriodData()})()
        self.wel = type("FakeWEL", (), {"stress_period_data": _StressPeriodData()})()

        existing_riv = np.array(
            [((0, 0, 1), 10.0, 250.0, 8.5)],
            dtype=[
                ("cellid", object),
                ("stage", float),
                ("cond", float),
                ("rbot", float),
            ],
        )
        self.riv = type(
            "FakeRIV",
            (),
            {"stress_period_data": _StressPeriodData(existing_riv)},
        )()


class TestMODFLOWInterface(unittest.TestCase):
    def make_interface(self):
        interface = MODFLOWInterface.__new__(MODFLOWInterface)
        interface.model = _FakeModel()
        interface.grid_info = {
            "nrow": 2,
            "ncol": 3,
            "nlayers": 1,
            "dx": 100.0,
            "dy": 100.0,
        }
        interface.boundary_manager = None
        return interface

    def test_cell_id_to_indices_uses_zero_based_row_major_order(self):
        interface = self.make_interface()

        self.assertEqual(interface._cell_id_to_indices(0), (0, 0, 0))
        self.assertEqual(interface._cell_id_to_indices(1), (0, 0, 1))
        self.assertEqual(interface._cell_id_to_indices(5), (0, 1, 2))

    def test_stress_update_converts_recharge_and_preserves_riv_fields(self):
        interface = self.make_interface()

        interface._update_stress_period(
            recharge_cells={1: 1000.0},
            et_cells={2: 500.0},
            pumping_cells={5: -75.0},
            channel_stages={1: 12.25},
        )

        _, recharge_array = interface.model.rch.recharge.calls[-1]
        self.assertAlmostEqual(recharge_array[0, 1], 0.1)

        _, et_spd = interface.model.evt.stress_period_data.calls[-1]
        self.assertEqual(et_spd, [((0, 0, 2), 0.0, 0.05, 3.0)])

        _, wel_spd = interface.model.wel.stress_period_data.calls[-1]
        self.assertEqual(wel_spd, [((0, 1, 2), -75.0)])

        _, riv_spd = interface.model.riv.stress_period_data.calls[-1]
        self.assertEqual(riv_spd, [((0, 0, 1), 12.25, 250.0, 8.5)])


if __name__ == "__main__":
    unittest.main()
