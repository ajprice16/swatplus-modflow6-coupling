"""Tests for the MODFLOW-2005 STR -> MF6 RIV converter (synthetic inputs only)."""
import sys
from pathlib import Path

import pytest

from swatplus_modflow.utils.str_to_riv import (
    StrFormatError,
    parse_str_file,
    str_to_riv_spd,
)

REPO = Path(__file__).resolve().parents[1]

# 2 segments, 3 reaches; NTRIB=1 and NDIV=1 so items 8 and 9 must be skipped.
# Elevations are negative on purpose: datum-relative stages are valid input.
SYNTHETIC_STR = """\
# synthetic STR file
3 2 1 1 0 1.0 0 0
3 0 0
1 1 1 1 1 10.0 -2.0 50.0 -3.0 -2.5
1 1 2 1 2  0.0 -2.2 40.0 -3.2 -2.7
1 2 2 2 1  0.0 -2.4 30.0 -3.4 -2.9
0
1
0
0
-1 0 0
0 0 0
"""


def _write(tmp_path, text, name="model.str"):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_parse_counts_and_zero_based_cells(tmp_path):
    pkg = parse_str_file(_write(tmp_path, SYNTHETIC_STR))
    assert (pkg.mxacts, pkg.nss, pkg.ntrib, pkg.ndiv, pkg.icalc) == (3, 2, 1, 1, 0)
    assert pkg.nper == 3
    assert len(pkg.periods[0]) == 3
    assert pkg.periods[1] is pkg.periods[0]          # ITMP < 0 reuses
    assert pkg.periods[2] == []                      # ITMP = 0 switches off
    r = pkg.periods[0][0]
    assert (r.layer, r.row, r.col, r.segment, r.reach) == (0, 0, 0, 1, 1)
    assert r.stage == -2.0 and r.sbot == -3.0        # negative elevations kept


def test_riv_records_keep_every_reach(tmp_path):
    spd = str_to_riv_spd(parse_str_file(_write(tmp_path, SYNTHETIC_STR)))
    assert [len(spd[p]) for p in range(3)] == [3, 3, 0]
    assert spd[0][2] == ((0, 1, 1), -2.4, 30.0, -3.4)


def test_nper_mismatch_raises(tmp_path):
    with pytest.raises(StrFormatError, match="only 3 stress periods"):
        parse_str_file(_write(tmp_path, SYNTHETIC_STR), nper=4)


def test_short_record_reports_its_own_line(tmp_path):
    bad = SYNTHETIC_STR.replace("1 1 2 1 2  0.0 -2.2 40.0 -3.2 -2.7", "1 1 2 1 2 0.0")
    with pytest.raises(StrFormatError, match=r"^line 5:"):
        parse_str_file(_write(tmp_path, bad))


def test_bottom_above_stage_raises(tmp_path):
    bad = SYNTHETIC_STR.replace("-2.0 50.0 -3.0", "-2.0 50.0 -1.0")
    with pytest.raises(StrFormatError, match="above stage"):
        str_to_riv_spd(parse_str_file(_write(tmp_path, bad)))


def test_parameterized_file_rejected(tmp_path):
    with pytest.raises(StrFormatError, match="parameterized"):
        parse_str_file(_write(tmp_path, "PARAMETER 1 3\n" + SYNTHETIC_STR))


def test_fixed_format_records(tmp_path):
    def rec(l, r, c, s, n, flow, stage, cond, sbot, stop):
        return f"{l:5d}{r:5d}{c:5d}{s:5d}{n:5d}{flow:15.3f}{stage:10.3f}{cond:10.3f}{sbot:10.3f}{stop:10.3f}"
    text = "1 1 0 0 0 1.0 0 0\n1 0 0\n" + rec(2, 3, 4, 1, 1, 5.0, 100.0, 12.5, 98.0, 99.0) + "\n"
    pkg = parse_str_file(_write(tmp_path, text), fixed_format=True)
    r = pkg.periods[0][0]
    assert (r.layer, r.row, r.col, r.stage, r.cond, r.sbot) == (1, 2, 3, 100.0, 12.5, 98.0)


def test_cli_writes_explicit_empty_period(tmp_path):
    flopy = pytest.importorskip("flopy")
    ws = tmp_path / "mf6"
    sim = flopy.mf6.MFSimulation(sim_name="sim", sim_ws=str(ws), exe_name="mf6")
    flopy.mf6.ModflowTdis(sim, nper=3, perioddata=[(1.0, 1, 1.0)] * 3)
    flopy.mf6.ModflowIms(sim)
    gwf = flopy.mf6.ModflowGwf(sim, modelname="gwf")
    flopy.mf6.ModflowGwfdis(gwf, nlay=1, nrow=2, ncol=2, delr=10.0, delc=10.0, top=0.0, botm=-10.0)
    flopy.mf6.ModflowGwfic(gwf, strt=-1.0)
    flopy.mf6.ModflowGwfnpf(gwf, k=1.0)
    sim.write_simulation(silent=True)

    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import convert_str_to_riv
    finally:
        sys.path.pop(0)
    rc = convert_str_to_riv.main(["--str-file", str(_write(tmp_path, SYNTHETIC_STR)), "--mf6-ws", str(ws)])
    assert rc == 0

    riv_files = list(ws.glob("*.riv"))
    assert len(riv_files) == 1
    text = riv_files[0].read_text().lower()
    for per in (1, 2, 3):
        assert f"begin period  {per}" in text or f"begin period {per}" in text
    period3 = text.split("period  3")[-1] if "period  3" in text else text.split("period 3")[-1]
    body3 = period3.split("end period")[0].strip()
    assert body3 == "", f"period 3 should be empty, got {body3!r}"
