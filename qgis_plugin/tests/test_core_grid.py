import json
import math

import pytest

from swatplus_modflow_qgis.core import grid as g

CRS = "EPSG:32633"  # arbitrary projected CRS used only to exercise the format


def make(nrow=4, ncol=5, d=100.0, x0=1000.0, y0=2000.0):
    return g.GridSpec(CRS, x0, y0, d, d, nrow, ncol)


def test_orientation_row1_north_col1_west():
    gr = make()
    assert gr.ytop == 2400.0 and gr.xmax == 1500.0
    assert gr.cell_bounds(1, 1) == (1000.0, 2300.0, 1100.0, 2400.0)
    assert gr.cell_bounds(4, 5) == (1400.0, 2000.0, 1500.0, 2100.0)
    assert gr.locate(1050.0, 2350.0) == (1, 1)
    assert gr.locate(1450.0, 2050.0) == (4, 5)
    assert gr.locate(1500.0, 2000.0) == (4, 5)      # outer edges belong to last cell
    assert gr.locate(1100.0, 2300.0) == (2, 2)      # interior boundary -> east/south cell
    assert gr.locate(999.0, 2100.0) is None and gr.locate(1200.0, 2400.1) is None


def test_json_roundtrip_and_contract_keys(tmp_path):
    gr = make()
    p = tmp_path / "grid.json"
    g.write_grid_json(gr, p)
    data = json.loads(p.read_text())
    assert set(data) == {"format", "crs", "xorigin", "yorigin", "angrot", "delr", "delc", "nrow", "ncol"}
    assert data["format"] == "swatplus-modflow-grid/1" and data["angrot"] == 0.0
    assert g.read_grid_json(p) == gr


@pytest.mark.parametrize("patch", [{"format": "swatplus-modflow-grid/2"}, {"format": "other/1"}, {"crs": "32633"},
                                   {"angrot": 15.0}, {"delr": 0.0}, {"nrow": 0}])
def test_rejects_bad_grids(patch):
    d = make().to_dict()
    d.update(patch)
    with pytest.raises(g.GridError):
        g.GridSpec.from_dict(d)


def test_grid_from_extent_snaps_and_covers():
    gr = g.grid_from_extent(CRS, 1013.0, 2021.0, 1388.0, 2290.0, 100.0, buffer=50.0)
    assert gr.xorigin % 100 == 0 and gr.yorigin % 100 == 0
    assert gr.xorigin <= 1013 - 50 and gr.xmax >= 1388 + 50
    assert gr.yorigin <= 2021 - 50 and gr.ytop >= 2290 + 50
    ns = g.grid_from_extent(CRS, 1013.0, 2021.0, 1388.0, 2290.0, 100.0, buffer=0.0, snap=False)
    assert ns.xorigin == 1013.0 and ns.yorigin == 2021.0
    with pytest.raises(g.GridError):
        g.grid_from_extent(CRS, 0, 0, 1e6, 1e6, 1.0)


def test_cell_range_clamps():
    gr = make()
    assert gr.cell_range(1050, 2050, 1150, 2150) == (3, 4, 1, 2)
    assert gr.cell_range(0, 0, 1e6, 1e6) == (1, 4, 1, 5)
    assert gr.cell_range(5000, 5000, 6000, 6000) is None


def test_split_polyline_horizontal_and_total_length():
    gr = make()
    # west -> east along the middle of row 2, starting 50 m west of the grid
    pieces = g.split_polyline([(950.0, 2250.0), (1350.0, 2250.0)], gr)
    assert [(p.row, p.col) for p in pieces] == [(None, None), (2, 1), (2, 2), (2, 3), (2, 4)]
    assert math.isclose(sum(p.length for p in pieces), 400.0)
    assert math.isclose(pieces[0].length, 50.0) and math.isclose(pieces[1].length, 100.0)
    assert math.isclose(pieces[-1].length, 50.0)


def test_split_polyline_diagonal_conserves_length_and_visits_in_order():
    gr = make()
    pts = [(1010.0, 2390.0), (1210.0, 2190.0), (1210.0, 2110.0)]
    pieces = g.split_polyline(pts, gr)
    assert math.isclose(sum(p.length for p in pieces), g.polyline_length(pts))
    assert pieces[0] == g.Piece(1, 1, pieces[0].length) and pieces[-1].row == 3
    rows = [p.row for p in pieces]
    assert rows == sorted(rows)  # row index never decreases when travelling south


def test_split_polyline_reentry_gives_two_pieces():
    gr = make()
    pts = [(1050.0, 2350.0), (1150.0, 2350.0), (1150.0, 2250.0), (1050.0, 2250.0), (1050.0, 2340.0)]
    pieces = g.split_polyline(pts, gr)
    cells = [(p.row, p.col) for p in pieces]
    assert cells.count((1, 1)) == 2
