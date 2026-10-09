"""
Executable enforcement of the four coupling integrity rules in ``.cursorrules``.

Each guard raises :class:`CouplingInvariantError` — a hard failure, not a
warning. The design intent is that a coupled run which violates mass balance,
loses temporal synchronisation, indexes outside the grid, or joins mismatched
coordinate systems **stops**, rather than completing and producing a
plausible-looking hydrograph that is quietly wrong.

Rules enforced
--------------
1. Mass balance     :func:`check_daily_mass_balance`, :func:`check_overlap_fractions`
2. Temporal sync    :func:`check_recharge_month_coverage`, :func:`check_forcing_length`
3. Spatial indices  :func:`check_cell_indices`, :func:`check_overlap_fractions`
4. Dims and CRS     :func:`check_array_dimensions`, :func:`check_crs_match`,
                    :func:`check_projected_crs`
Convergence         :func:`check_convergence`

Every guard is pure and cheap; they are meant to be called inside the daily
loop without measurable cost. ``check_daily_mass_balance`` is the only one
that touches simulation state.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Mapping, Sequence

import numpy as np

logger = logging.getLogger(__name__)

__all__ = [
    "CouplingInvariantError",
    "check_array_dimensions",
    "check_cell_indices",
    "check_convergence",
    "check_crs_match",
    "check_daily_mass_balance",
    "check_forcing_length",
    "check_overlap_fractions",
    "check_projected_crs",
    "check_recharge_month_coverage",
]

# Fractions are area ratios from a polygon intersection; 1e-3 absorbs
# shapefile rounding without hiding a real partition error.
FRACTION_TOL = 1.0e-3

# Common geographic (degree-unit) CRS codes. Area-weighted overlap in these
# is latitude-dependent and therefore invalid.
_GEOGRAPHIC_EPSG = {"4326", "4269", "4267", "4258", "4979"}


class CouplingInvariantError(RuntimeError):
    """A coupling integrity rule was violated. The run must not continue."""


# ─── Rule 4: dimensions and coordinate reference systems ─────────────────────

def check_array_dimensions(
    a: Any,
    b: Any,
    names: Sequence[str] = ("array a", "array b"),
) -> None:
    """
    Assert two arrays have identical shape before a cross-model calculation.

    Guards against NumPy broadcasting, which turns a length mismatch into a
    silent fan-out (a length-1 forcing vector filling every reach) instead of
        an error.
    """
    sa = np.shape(np.asarray(a))
    sb = np.shape(np.asarray(b))
    if sa != sb:
        raise CouplingInvariantError(
            f"dimension mismatch: {names[0]} has shape {sa}, "
            f"{names[1]} has shape {sb}. Rule 4 requires matching array "
            "dimensions before any cross-model calculation."
        )


def check_forcing_length(n_rows: int, n_steps: int, name: str = "forcing") -> None:
    """
    Assert a daily forcing table has exactly one row per simulated timestep.

    Called before the API loop so a TDIS/forcing span mismatch fails in
    milliseconds rather than as an ``IndexError`` hours into the run.
    """
    if int(n_rows) != int(n_steps):
        raise CouplingInvariantError(
            f"temporal mismatch: {name} has {n_rows} rows but the simulation "
            f"will take {n_steps} timesteps. Rule 2 requires exact "
            "day-to-stress-period correspondence."
        )


def _normalise_crs(crs: Any) -> str | None:
    if crs is None:
        return None
    text = str(getattr(crs, "srs", crs)).strip()
    return text.upper() or None


def check_crs_match(
    crs_a: Any,
    crs_b: Any,
    names: Sequence[str] = ("dataset a", "dataset b"),
) -> None:
    """Assert two spatial datasets share a coordinate reference system."""
    na, nb = _normalise_crs(crs_a), _normalise_crs(crs_b)
    if na is None or nb is None:
        undefined = names[0] if na is None else names[1]
        raise CouplingInvariantError(
            f"undefined CRS on {undefined}. Rule 4 requires an explicit "
            "coordinate reference system on both datasets before a spatial "
            "join; an assumed CRS is a silent mis-registration."
        )
    if na != nb:
        raise CouplingInvariantError(
            f"CRS mismatch: {names[0]} is {na}, {names[1]} is {nb}. "
            "Reproject before intersecting — Rule 4."
        )


def check_projected_crs(crs: Any, name: str = "dataset") -> None:
    """
    Assert a CRS is projected (linear units), as area-weighted overlap requires.
    """
    text = _normalise_crs(crs)
    if text is None:
        raise CouplingInvariantError(f"undefined CRS on {name}.")
    code = text.split(":")[-1]
    is_geographic = code in _GEOGRAPHIC_EPSG or "DEGREE" in text
    if hasattr(crs, "is_geographic"):
        is_geographic = bool(crs.is_geographic)
    if is_geographic:
        raise CouplingInvariantError(
            f"{name} uses a geographic CRS ({text}); overlap fractions are "
            "area-weighted and areas in degrees are latitude-dependent. "
            "Reproject to a projected CRS with linear units."
        )


# ─── Rule 3: strict index validation ─────────────────────────────────────────

def check_cell_indices(
    cell_ids: Iterable[int],
    grid: Mapping[str, int],
    name: str = "cell ids",
) -> None:
    """
    Assert every cell id is a valid zero-based node number for ``grid``.

    ``grid`` needs ``nlay``, ``nrow``, ``ncol``. Catches both the negative
    ids produced by mixing one-based rows into the linear index formula (they
    wrap to the end of a MODFLOW pointer array) and ids past the last node.
    """
    n_nodes = int(grid["nlay"]) * int(grid["nrow"]) * int(grid["ncol"])
    ids = list(cell_ids)
    negative = [i for i in ids if i < 0]
    if negative:
        raise CouplingInvariantError(
            f"negative {name}: {negative[:10]}. A negative index wraps to the "
            "end of the target array instead of raising — usually a one-based "
            "row/col fed into a zero-based linear index. Rule 3."
        )
    over = [i for i in ids if i >= n_nodes]
    if over:
        raise CouplingInvariantError(
            f"{name} out of range: {over[:10]} >= n_nodes={n_nodes} "
            f"({grid['nlay']}x{grid['nrow']}x{grid['ncol']}). Rule 3 requires "
            "strict index validation."
        )


def check_overlap_fractions(
    geo_map: Mapping[Any, Sequence[tuple]],
    tol: float = FRACTION_TOL,
    name: str = "source feature",
) -> None:
    """
    Assert each source feature's overlap fractions partition it exactly.

    Fractions summing above 1 multiply the feature's flux (creating water);
    below 1 they discard it. Both violate Rule 1, and neither is currently
    checked anywhere in the coupling path.
    """
    for key, conns in geo_map.items():
        if not conns:
            raise CouplingInvariantError(
                f"{name} {key} has no intersecting grid cells; that part of "
                "the watershed is decoupled. Rule 3."
            )
        fracs = [float(f) for _, f in conns]
        if any(f < 0 for f in fracs):
            raise CouplingInvariantError(
                f"negative overlap fraction on {name} {key}: {fracs}. Rule 3."
            )
        total = sum(fracs)
        if abs(total - 1.0) > tol:
            verb = "creates" if total > 1.0 else "destroys"
            raise CouplingInvariantError(
                f"overlap fractions for {name} {key} sum to {total:.3f}, not "
                f"1.000 — disaggregation {verb} water. Rule 1."
            )


# ─── Rule 2: temporal synchronisation ────────────────────────────────────────

def check_recharge_month_coverage(
    dates: Iterable[Any],
    rch_by_month: Mapping[tuple, Any],
) -> None:
    """
    Assert every simulated month has its own recharge array.

    The daily loop applies recharge only when the month key is present, so a
    gap silently leaves the previous month's array in the MODFLOW pointer —
    a lagged forcing that persists until the next present month.
    """
    needed = {(d.year, d.month) for d in dates}
    missing = sorted(needed - set(rch_by_month))
    if missing:
        shown = ", ".join(f"{y}-{m:02d}" for y, m in missing[:12])
        raise CouplingInvariantError(
            f"{len(missing)} simulated month(s) have no recharge array "
            f"({shown}). The loop would silently reuse the previous month's "
            "recharge — a lagged forcing. Rule 2."
        )


# ─── Convergence ─────────────────────────────────────────────────────────────

def check_convergence(
    converged: bool,
    date: Any,
    kiter: int,
    max_iter: int,
    *,
    allow_failures: int = 0,
    n_failed_so_far: int = 0,
) -> None:
    """
    Assert the day's MODFLOW solution converged.

    A non-converged day leaves heads at whatever the last outer iteration
    produced, so both the head field and every flux derived from it are
    unphysical — and because the coupling is sequential, the error is carried
    into the next day's SWAT+ forcing. ``allow_failures`` exists only for
    equilibration runs where early instability is expected; the default is
    zero tolerance.
    """
    if converged:
        return
    if n_failed_so_far < allow_failures:
        logger.warning(
            "MODFLOW did not converge on %s after %d/%d outer iterations "
            "(failure %d of %d tolerated)",
            date, kiter, max_iter, n_failed_so_far + 1, allow_failures,
        )
        return
    raise CouplingInvariantError(
        f"MODFLOW failed to converge on {date} after {kiter}/{max_iter} outer "
        "iterations. Heads and all derived exchange fluxes for this day are "
        "unphysical and would propagate into tomorrow's SWAT+ forcing. "
        "Halting rather than reporting a plausible-looking hydrograph. "
        "Loosen IMS settings, shorten the timestep, or pass "
        "allow_failures>0 for a deliberate equilibration run."
    )


# ─── Rule 1: mass balance ────────────────────────────────────────────────────

def check_daily_mass_balance(
    budget: Any,
    *,
    tolerance_percent: float = 1.0,
    threshold_m3: float = 1.0,
    date: Any = None,
) -> float:
    """
    Assert one day's water budget closes within ``tolerance_percent``.

    Returns the percent error so the caller can log or accumulate it. Raises
    when the day exceeds tolerance — the per-day gate matters because signed
    residuals cancel across days, so a cumulative check can read clean while
    individual days create and destroy water.
    """
    percent = budget.get_mass_balance_percent_error(threshold_m3=threshold_m3)
    if percent > tolerance_percent:
        when = date if date is not None else getattr(budget, "date", "unknown date")
        raise CouplingInvariantError(
            f"mass-balance violation on {when}: {percent:.3f}% error exceeds "
            f"the {tolerance_percent:.3f}% tolerance "
            f"(residual {budget.compute_mass_balance_error():,.1f} m3/day, "
            f"net aquifer flux {budget.compute_net_aquifer_flux():,.1f} "
            "m3/day). Rule 1 forbids data transformations that create or "
            "destroy water."
        )
    return percent
