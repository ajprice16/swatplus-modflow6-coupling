"""Build the daily forcing the driver injects into MODFLOW 6.

Units throughout: SWAT+ percolation mm/day over the ``hru.con`` area; volumes
m3/day; MODFLOW recharge rates m/day over each cell's area; SFR runoff m3/day.
"""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import scipy.sparse as sp

from .errors import EngineInputError
from .swat_io import upstream_with_output

log = logging.getLogger(__name__)


def day_list(start: date, n: int) -> List[date]:
    return [start + timedelta(days=i) for i in range(n)]


# ---------------------------------------------------------------- recharge

def apply_recharge_delay(perc: np.ndarray, delay_days: float) -> np.ndarray:
    """Exponential vadose-zone delay on daily percolation, per HRU (SWAT ``gw_delay`` form).

    ``r[n] = (1 - exp(-1/delay)) * perc[n] + exp(-1/delay) * r[n-1]``, ``r[-1] = 0``.
    The volume still in transit at the end of the series is ``perc.sum() - r.sum()``.
    ``delay_days == 0`` returns the input unchanged. ``perc`` is ``(n_days, n_hru)``.
    """
    if delay_days <= 0:
        return perc
    a = float(np.exp(-1.0 / delay_days))
    out = np.empty_like(perc)
    prev = np.zeros(perc.shape[1], dtype=perc.dtype)
    for i in range(perc.shape[0]):
        prev = (1.0 - a) * perc[i] + a * prev
        out[i] = prev
    return out


def build_weights(hru_ids: Sequence[int], cells, hru_area_m2: Dict[int, float],
                  cell_area_m2, nrow: int, ncol: int) -> Tuple[sp.csr_matrix, np.ndarray]:
    """Sparse map from HRU percolation depth (m/day) to column recharge rate (m/day).

    ``rate[col] = sum_h W[col, h] * perc_m[h]`` with
    ``W = area_hru_con * fraction / cell_area``. Returns ``(W, column_layer)`` where
    ``column_layer[col]`` is the 0-based layer of the table's rows for that column
    (-1 when no HRU touches it); a column fed with two different layers is an error.
    """
    idx = {int(h): j for j, h in enumerate(hru_ids)}
    ncell = nrow * ncol
    cell_area = np.broadcast_to(np.asarray(cell_area_m2, dtype=float).reshape(-1), (ncell,))
    r, c, v = [], [], []
    layer_of = np.full(ncell, -1, dtype=np.int64)
    for h, lay, ro, co, fr in zip(cells.hru_id, cells.layer, cells.row, cells.col, cells.fraction):
        j = idx.get(int(h))
        if j is None:
            continue
        col = int(ro) * ncol + int(co)
        if layer_of[col] not in (-1, int(lay)):
            raise EngineInputError(
                f"hru_cells: cell column (row {ro + 1}, col {co + 1}) receives recharge in layers "
                f"{layer_of[col] + 1} and {int(lay) + 1}; one layer per column is required")
        layer_of[col] = int(lay)
        r.append(col)
        c.append(j)
        v.append(hru_area_m2[int(h)] * float(fr) / cell_area[col])
    W = sp.csr_matrix((v, (r, c)), shape=(ncell, len(hru_ids)))
    return W, layer_of


def ramped_month_recharge(prev: np.ndarray, cur: np.ndarray, nxt: np.ndarray,
                          n_prev: int, n_cur: int, n_next: int) -> np.ndarray:
    """Daily rates ``(n_cur, ncell)`` ramped linearly between month midpoints.

    Each cell is rescaled so the month's volume equals ``cur * n_cur`` exactly. A monthly
    step swaps in the next month's whole rate on one day; the ramp avoids that jump.
    Linear interpolation of non-negative rates is clipped at zero, so rates stay
    non-negative and a cell with ``cur == 0`` gets 0.
    """
    j = np.arange(1, n_cur + 1, dtype=np.float64)[:, None]
    mid = (n_cur + 1) / 2.0
    lo = cur + (j - mid) / ((n_prev + n_cur) / 2.0) * (cur - prev)
    hi = cur + (j - mid) / ((n_cur + n_next) / 2.0) * (nxt - cur)
    r = np.clip(np.where(j < mid, lo, hi), 0.0, None)
    tot = r.sum(axis=0)
    want = cur * n_cur
    scale = np.divide(want, tot, out=np.zeros_like(tot, dtype=np.float64), where=tot > 0)
    out = r * scale
    flat = (tot <= 0) & (want > 0)
    out[:, flat] = cur[flat]
    return out


class RechargeSchedule:
    """Daily column recharge rates (m/day) from daily HRU percolation.

    ``update`` is ``daily`` (each day's own rate), ``monthly`` (the month's mean
    daily rate held for the month) or ``monthly_ramped``. In the monthly modes
    the volume over the simulated days of each month equals the daily volume.
    """

    def __init__(self, perc_mm: np.ndarray, dates: Sequence[date], W: sp.csr_matrix, update: str):
        if perc_mm.shape[0] != len(dates):
            raise EngineInputError(f"percolation has {perc_mm.shape[0]} days, expected {len(dates)}")
        if update not in ("daily", "monthly", "monthly_ramped"):
            raise EngineInputError(f"unknown recharge_update '{update}'")
        self.dates, self.W, self.update = list(dates), W, update
        self.perc_m = perc_mm * 1.0e-3
        self._month_cache: Dict[Tuple[int, int], np.ndarray] = {}
        self._months: Dict[Tuple[int, int], List[int]] = {}
        for i, d in enumerate(self.dates):
            self._months.setdefault((d.year, d.month), []).append(i)

    def _month_mean_hru(self, key) -> Optional[np.ndarray]:
        idx = self._months.get(key)
        return None if not idx else self.perc_m[idx].mean(axis=0)

    def rates(self, i: int) -> np.ndarray:
        """Column recharge rates (m/day) for day index ``i``."""
        if self.update == "daily":
            return self.W @ self.perc_m[i]
        d = self.dates[i]
        key = (d.year, d.month)
        if key not in self._month_cache:
            idx = self._months[key]
            cur_h = self._month_mean_hru(key)
            cur = self.W @ cur_h
            if self.update == "monthly":
                block = np.repeat(cur[None, :], len(idx), axis=0)
            else:
                pk = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
                nk = (d.year, d.month + 1) if d.month < 12 else (d.year + 1, 1)
                ph, nh = self._month_mean_hru(pk), self._month_mean_hru(nk)
                prev = self.W @ ph if ph is not None else cur
                nxt = self.W @ nh if nh is not None else cur
                full = ramped_month_recharge(prev, cur, nxt, calendar.monthrange(*pk)[1],
                                             calendar.monthrange(*key)[1], calendar.monthrange(*nk)[1])
                days_in = [self.dates[k].day - 1 for k in idx]
                block = full[days_in]
                tot, want = block.sum(axis=0), cur * len(idx)      # keep the simulated days' volume
                block = block * np.divide(want, tot, out=np.ones_like(tot), where=tot > 0)
            self._month_cache = {key: block}                        # one month at a time
            self._month_first = idx[0]
        return self._month_cache[key][i - self._month_first]


# ---------------------------------------------------------------- SFR runoff

@dataclass
class RunoffResult:
    runoff_m3d: np.ndarray          # (n_days, n_reaches)
    loss_reach_m3d: Optional[np.ndarray]   # (n_days, n_reaches) transmission loss, or None
    unmapped_m3: float              # net local flow of SWAT+ channels without a reach (not injected)
    seep_added_m3: float
    # (n_days,) share of the injected runoff that belongs to the length of channels lying
    # OUTSIDE the grid; it is injected into the same channel's in-grid reaches. None when the
    # in-grid fractions are unknown (no connections.channel_cells).
    offgrid_reassigned_m3d: Optional[np.ndarray] = None


def build_runoff(flow_m3s: np.ndarray, seep_m3d: Optional[np.ndarray], channel_ids: Sequence[int],
                 chan_reaches: Dict[int, List[Tuple[int, float]]], upstream_of: Dict[int, List[int]],
                 n_reaches: int, *, trans: Optional[dict] = None,
                 ingrid_fraction: Optional[Dict[int, float]] = None) -> RunoffResult:
    """Each channel's local flow, spread over its reaches by length, as SFR RUNOFF (m3/day).

    ``local = flo_out - sum(flo_out of the nearest upstream channels with output)``. SWAT+
    ``seep`` (its own channel-to-aquifer loss) is added back because the SFR streambed now
    carries all channel-aquifer exchange; leaving it in would count the loss twice.
    Evaporation and storage change stay in ``local`` as negative runoff, which MF6 caps at
    the available streamflow. Nothing is clamped, so the injected total equals SWAT+'s net.

    ``trans`` (optional) = dict(losing=set of channel ids, len_km={ch: km}, wd_m={ch: m},
    k_in_per_h=float, duration_h=float). For losing channels the positive local flow is
    reduced by the Lane loss, returned per reach so the caller can add it to recharge.

    Channels partly outside the grid: the whole local flow of a channel is spread over its
    in-grid reaches (shares from sfr_reaches.csv sum to 1), so SWAT+ channel water is
    conserved. This is a deliberate modelling choice -- unlike HRU percolation outside the
    grid, which leaves the model. When ``ingrid_fraction`` ({channel_id: in-grid length
    fraction from channel_cells.csv}) is given, the part attributable to the off-grid length,
    ``q * (1 - f)``, is reported per day in ``offgrid_reassigned_m3d``.
    """
    from . import transmission as lane

    n_days = flow_m3s.shape[0]
    col = {int(c): j for j, c in enumerate(channel_ids)}
    present = set(col)
    runoff = np.zeros((n_days, n_reaches))
    loss_reach = np.zeros((n_days, n_reaches)) if trans is not None else None
    unmapped, seep_total = 0.0, 0.0
    offgrid = np.zeros(n_days) if ingrid_fraction is not None else None
    for cid, j in col.items():
        q = flow_m3s[:, j] * 86400.0
        if np.isnan(q).any():
            raise EngineInputError(f"SWAT+ channel output has missing days for channel {cid}")
        for up in upstream_with_output(cid, upstream_of, present):
            q = q - flow_m3s[:, col[up]] * 86400.0
        if seep_m3d is not None:
            s = seep_m3d[:, j]
            q = q + s
            seep_total += float(np.nansum(s))
        reaches = chan_reaches.get(cid)
        if not reaches:
            unmapped += float(q.sum())
            continue
        loss = None
        if trans is not None and cid in trans["losing"]:
            qp = np.clip(q, 0.0, None)
            loss = qp - lane.lane_outflow_lateral(qp, trans["len_km"][cid], trans["wd_m"][cid],
                                                  trans["k_in_per_h"], trans["duration_h"])
            q = q - loss
        if offgrid is not None:
            f = min(max(float(ingrid_fraction.get(cid, 1.0)), 0.0), 1.0)
            if f < 1.0 - 1e-9:   # a full in-grid fraction can sum to 1 - 1e-16 in float
                offgrid += q * (1.0 - f)
        for rno, frac in reaches:
            runoff[:, rno] += q * frac
            if loss is not None:
                loss_reach[:, rno] += loss * frac
    return RunoffResult(runoff, loss_reach, unmapped, seep_total, offgrid)
