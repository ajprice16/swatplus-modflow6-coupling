"""
Invariant tests for .cursorrules rules 3 and 4 — SPATIAL COHERENCE and
DIMENSIONAL / CRS VALIDATION.

    rule 3: "Handle spatial boundaries and grid intersections dynamically.
             Validate array indices strictly."
    rule 4: "Assert that array dimensions and coordinate reference systems
             match before performing cross-model calculations."

These pin the mapper's index arithmetic, the grid-bounds contract, and the
dimension/CRS assertions the rules require before any cross-model transfer.

Defect ids are catalogued in the audit report.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from swatplus_modflow.coupling.flux_translator import FluxTranslator
from swatplus_modflow.coupling.geographic_mapper import GeographicMapper


def _mapper(nrow: int = 10, ncol: int = 10, nlay: int = 1, index_base: int = 0):
    """A GeographicMapper with only the grid metadata the index math needs."""
    gm = GeographicMapper.__new__(GeographicMapper)
    gm.modflow_grid = {
        "nrow": nrow, "ncol": ncol, "nlay": nlay, "index_base": index_base,
    }
    return gm


# ─── Cell-index arithmetic ───────────────────────────────────────────────────

def test_corner_cells_map_to_the_expected_ids():
    """
    Canonical usage: layer is 1-based (callers pass ``row.get("mf_layer", 1)``)
    while row/col honour ``index_base``.
    """
    gm = _mapper(10, 10, index_base=0)
    assert gm._cell_id_from_row(1, 0, 0) == 0
    assert gm._cell_id_from_row(1, 0, 9) == 9
    assert gm._cell_id_from_row(1, 1, 0) == 10
    assert gm._cell_id_from_row(1, 9, 9) == 99

    one = _mapper(10, 10, index_base=1)
    assert one._cell_id_from_row(1, 1, 1) == 0
    assert one._cell_id_from_row(1, 10, 10) == 99


def test_cell_ids_are_a_bijection_over_the_grid():
    """
    Every (layer, row, col) in range must map to a distinct id inside
    [0, nlay*nrow*ncol). A collision aliases two aquifer cells onto one
    forcing slot; an out-of-range id writes past the MODFLOW array.
    """
    nlay, nrow, ncol = 2, 7, 5
    gm = _mapper(nrow, ncol, nlay, index_base=0)
    ids = [
        gm._cell_id_from_row(k, i, j)
        for k in range(1, nlay + 1) for i in range(nrow) for j in range(ncol)
    ]
    n = nlay * nrow * ncol
    assert len(set(ids)) == n, (
        f"{n - len(set(ids))} colliding cell ids over a {nlay}x{nrow}x{ncol} grid"
    )
    assert min(ids) >= 0 and max(ids) < n, (
        f"cell ids span [{min(ids)}, {max(ids)}], outside [0, {n})"
    )


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT-15 (moderate): _cell_id_from_row() uses a MIXED index "
    "convention. index_base IS applied to row and col "
    "(row_idx = row - index_base, col_idx = col - index_base) but NOT to "
    "layer, which is unconditionally decremented (layer_idx = layer - 1). So "
    "a caller passing a zero-based layer gets layer_idx = -1 and a cell id of "
    "-nrow*ncol (-100 on a 10x10 grid) whatever index_base says. Negative "
    "ids wrap to the END of any MODFLOW pointer array instead of raising. "
    "Either apply index_base to the layer term as well, or document that "
    "layer is always 1-based while row/col are configurable."
))
def test_layer_indexing_honours_index_base_like_row_and_col():
    gm = _mapper(10, 10, index_base=0)
    got = gm._cell_id_from_row(0, 0, 0)
    assert got >= 0, (
        f"zero-based layer 0 on an index_base=0 grid mapped to {got}"
    )


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT-15 (moderate), second path: because index_base is subtracted from "
    "row and col, a grid declared index_base=1 that is fed ZERO-based row/col "
    "(the usual shapefile/GeoPandas convention) drives both indices to -1 and "
    "returns -11 on a 10x10 grid. _validate_index_base() only logs a warning, "
    "so the mismatch reaches the pointer write as a negative index."
))
def test_zero_based_rowcol_against_a_one_based_grid_is_rejected():
    gm = _mapper(10, 10, index_base=1)
    got = gm._cell_id_from_row(1, 0, 0)
    assert got >= 0, (
        f"zero-based row/col on an index_base=1 grid mapped to {got}"
    )


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT-16 (major): _cell_id_from_row() performs no bounds check, so "
    "row/col values outside the grid return an id beyond nlay*nrow*ncol "
    "(row=99,col=99 on a 10x10 grid -> 1089). Writing that index into an "
    "XMI pointer array is an out-of-bounds write or a silent misallocation "
    "of flux to the wrong cell. _validate_index_base() only logs a warning."
))
def test_out_of_range_row_col_is_rejected():
    gm = _mapper(10, 10, index_base=0)
    with pytest.raises((ValueError, IndexError, AssertionError)):
        gm._cell_id_from_row(1, 99, 99)


def test_index_base_mismatch_is_detected_not_merely_logged():
    """
    A shapefile join that emits one-based rows against a zero-based grid must
    fail loudly. _validate_index_base() currently logs and continues.
    """
    from swatplus_modflow.utils.coupling_invariants import (
        CouplingInvariantError,
        check_cell_indices,
    )

    grid = {"nlay": 1, "nrow": 10, "ncol": 10}
    check_cell_indices([0, 9, 99], grid)  # valid zero-based ids
    with pytest.raises(CouplingInvariantError, match="out of range"):
        check_cell_indices([0, 100], grid)
    with pytest.raises(CouplingInvariantError, match="negative"):
        check_cell_indices([-11, 5], grid)


# ─── Overlap-fraction validity (rule 3: grid intersections) ──────────────────

def test_overlap_fractions_must_partition_each_source_feature():
    from swatplus_modflow.utils.coupling_invariants import (
        CouplingInvariantError,
        check_overlap_fractions,
    )

    check_overlap_fractions({1: [(0, 0.6), (1, 0.4)]})          # exact
    check_overlap_fractions({1: [(0, 0.6), (1, 0.4001)]})       # within tol
    with pytest.raises(CouplingInvariantError, match="1.400"):
        check_overlap_fractions({1: [(0, 0.7), (1, 0.7)]})      # creates mass
    with pytest.raises(CouplingInvariantError, match="0.500"):
        check_overlap_fractions({1: [(0, 0.5)]})                # destroys mass
    with pytest.raises(CouplingInvariantError, match="negative"):
        check_overlap_fractions({1: [(0, 1.2), (1, -0.2)]})


def test_empty_mapping_for_a_source_feature_is_flagged():
    """
    An HRU or channel with no intersecting cells contributes nothing. Silent
    today; it means part of the watershed is decoupled.
    """
    from swatplus_modflow.utils.coupling_invariants import (
        CouplingInvariantError,
        check_overlap_fractions,
    )

    with pytest.raises(CouplingInvariantError, match="no intersecting"):
        check_overlap_fractions({1: [(0, 1.0)], 2: []})


# ─── Dimension and CRS assertions (rule 4) ───────────────────────────────────

def test_array_dimensions_must_match_before_cross_model_transfer():
    from swatplus_modflow.utils.coupling_invariants import (
        CouplingInvariantError,
        check_array_dimensions,
    )

    check_array_dimensions(np.zeros(5), np.zeros(5), names=("runoff", "reaches"))
    with pytest.raises(CouplingInvariantError, match=r"runoff.*5.*reaches.*7"):
        check_array_dimensions(np.zeros(5), np.zeros(7), names=("runoff", "reaches"))


def test_forcing_vector_length_must_equal_the_modflow_pointer_length():
    """
    ``runoff_ptr[:] = runoff[i]`` broadcasts silently when the lengths differ
    by a factor — a length-1 vector fills every reach with the same flow.
    """
    from swatplus_modflow.utils.coupling_invariants import (
        CouplingInvariantError,
        check_array_dimensions,
    )

    ptr = np.zeros(48, dtype=np.float64)
    good = np.arange(48, dtype=np.float64)
    check_array_dimensions(good, ptr, names=("daily runoff row", "SFR RUNOFF pointer"))
    scalarish = np.zeros(1)
    with pytest.raises(CouplingInvariantError):
        check_array_dimensions(scalarish, ptr, names=("daily runoff row", "SFR RUNOFF pointer"))


def test_crs_must_match_before_spatial_join():
    from swatplus_modflow.utils.coupling_invariants import (
        CouplingInvariantError,
        check_crs_match,
    )

    check_crs_match("EPSG:32613", "EPSG:32613", names=("HRUs", "MODFLOW grid"))
    check_crs_match("epsg:32613", "EPSG:32613", names=("HRUs", "MODFLOW grid"))
    with pytest.raises(CouplingInvariantError, match="EPSG:4326"):
        check_crs_match("EPSG:4326", "EPSG:32613", names=("HRUs", "MODFLOW grid"))


def test_geographic_crs_is_rejected_for_area_weighted_mapping():
    """
    Overlap fractions are area-weighted. Computing areas in degrees is
    latitude-dependent nonsense, so a geographic CRS must be refused rather
    than silently producing wrong weights.
    """
    from swatplus_modflow.utils.coupling_invariants import (
        CouplingInvariantError,
        check_projected_crs,
    )

    check_projected_crs("EPSG:32613", name="MODFLOW grid")
    with pytest.raises(CouplingInvariantError, match="geographic"):
        check_projected_crs("EPSG:4326", name="MODFLOW grid")


def test_missing_crs_is_an_error_not_an_assumption():
    from swatplus_modflow.utils.coupling_invariants import (
        CouplingInvariantError,
        check_crs_match,
    )

    with pytest.raises(CouplingInvariantError, match="undefined"):
        check_crs_match(None, "EPSG:32613", names=("HRUs", "MODFLOW grid"))


# ─── Streambed geometry (rule 3: boundaries are geometry, not state) ─────────

@pytest.mark.xfail(strict=True, reason=(
    "DEFECT-17 (major): the two functions that emit RIV-style records — "
    "translate_canal_stage() and translate_reservoir_stage() — derive rbot "
    "from the CURRENT stage (rbot = stage - max(bed_thickness_m, 1.0)), so "
    "the streambed bottom rises and falls with the water level. Streambed "
    "elevation is fixed geometry; a stage-following rbot holds the wetted "
    "depth constant at the bed thickness and makes RIV leakage insensitive "
    "to stage, defeating the conductance calibration. Both should take rbot "
    "from surveyed bed elevation (the canal path already accepts "
    "canal_bed_elevation, but uses it only to floor the stage). Note "
    "translate_channel_stage() is NOT affected: it returns cell -> stage "
    "only and computes no streambed geometry."
))
def test_streambed_bottom_is_fixed_geometry_not_a_function_of_stage():
    ft = FluxTranslator()
    geo = {1: [(0, 500.0)]}
    lo = ft.translate_canal_stage({1: 100.0}, geo, canal_bed_thickness_m={1: 1.0})
    hi = ft.translate_canal_stage({1: 105.0}, geo, canal_bed_thickness_m={1: 1.0})
    cell = next(iter(lo))
    assert lo[cell]["rbot"] == pytest.approx(hi[cell]["rbot"]), (
        f"rbot moved from {lo[cell]['rbot']:.1f} to {hi[cell]['rbot']:.1f} "
        "when stage rose 5 m"
    )


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT-17 (major): translate_reservoir_stage() computes the same "
    "stage-following rbot as the canal path."
))
def test_reservoir_streambed_bottom_is_fixed_geometry():
    ft = FluxTranslator()
    geo = {1: [(0, 1.0)]}
    lo = ft.translate_reservoir_stage(
        {1: 100.0}, geo, reservoir_bed_thickness_m={1: 1.0}
    )
    hi = ft.translate_reservoir_stage(
        {1: 105.0}, geo, reservoir_bed_thickness_m={1: 1.0}
    )
    assert lo and hi, "translate_reservoir_stage returned no RIV records"
    cell = next(iter(lo))
    assert "rbot" in lo[cell], f"no rbot key in {sorted(lo[cell])}"
    assert lo[cell]["rbot"] == pytest.approx(hi[cell]["rbot"]), (
        f"rbot moved from {lo[cell]['rbot']:.1f} to {hi[cell]['rbot']:.1f} "
        "when reservoir stage rose 5 m"
    )


def test_channel_stage_translation_emits_no_streambed_geometry():
    """
    Pins the scope of DEFECT-17: translate_channel_stage() returns
    cell -> stage only. It is NOT one of the affected functions, and a future
    change that starts emitting a stage-derived rbot here should trip this.
    """
    ft = FluxTranslator()
    out = ft.translate_channel_stage({1: 100.0}, {1: [(0, 500.0)]})
    for cell, value in out.items():
        assert isinstance(value, (int, float)), (
            f"cell {cell} returned {type(value).__name__}, not a bare stage; "
            "if this function now emits RIV records, apply the DEFECT-17 "
            "fixed-geometry rbot check to it too."
        )


def test_river_stage_must_not_fall_below_its_own_streambed():
    """A RIV cell with stage < rbot is physically inconsistent."""
    ft = FluxTranslator()
    riv = ft.translate_canal_stage(
        {1: 100.0}, {1: [(0, 500.0)]}, canal_bed_thickness_m={1: 1.0}
    )
    for cell, r in riv.items():
        assert r["stage"] >= r["rbot"], (
            f"cell {cell}: stage {r['stage']} below rbot {r['rbot']}"
        )


def test_conductance_is_strictly_positive():
    """Zero or negative RIV conductance silently disables the exchange."""
    ft = FluxTranslator()
    riv = ft.translate_canal_stage(
        {1: 100.0}, {1: [(0, 500.0)]}, canal_bed_thickness_m={1: 1.0}
    )
    for cell, r in riv.items():
        assert r["conductance"] > 0, (
            f"cell {cell}: non-positive conductance {r['conductance']}"
        )
