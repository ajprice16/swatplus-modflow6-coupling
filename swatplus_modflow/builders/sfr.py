"""Build MODFLOW 6 SFR data from ``channel_cells.csv`` and the channel topology.

Reach numbers are assigned in topological order, so every channel comes after the
channels that flow into it. That also guarantees reach 0 is never the target of a
downstream link, which avoids the ``-0 == 0`` ambiguity of signed connection data.
The reach <-> channel table written for the engine (``sfr_reaches.csv``) numbers
reaches from 1, as in the SFR input file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .grid import BuildInputError, Grid


@dataclass
class SfrBuild:
    packagedata: List[list]
    connectiondata: List[list]
    reaches: pd.DataFrame                  # rno (1-based), channel_id, seq, layer, row, col, length_m (1-based idx)
    notes: List[str] = field(default_factory=list)
    stats: Dict[str, float] = field(default_factory=dict)


def downstream_from_topology(upstream_of: Dict[int, List[int]]) -> Dict[int, int]:
    """Channel -> nearest downstream CHANNEL (0 = outlet), following through reservoirs.

    ``upstream_of`` is as returned by ``engine.swat_io.read_connection_topology``
    (positive keys = channels, negative keys = reservoirs).
    """
    down_nodes: Dict[int, List[int]] = {}
    for tgt, srcs in upstream_of.items():
        for s in srcs:
            down_nodes.setdefault(s, []).append(tgt)
    out: Dict[int, int] = {}
    chans = {n for n in set(down_nodes) | set(upstream_of) if n > 0}
    for c in sorted(chans):
        cur, seen, found = c, set(), 0
        while True:
            nxt = down_nodes.get(cur)
            if not nxt or cur in seen:
                break
            seen.add(cur)
            cur = nxt[0]
            if cur > 0:
                found = cur
                break
        out[c] = found
    return out


def _topological_order(channels: List[int], down: Dict[int, int]) -> List[int]:
    indeg = {c: 0 for c in channels}
    for c in channels:
        if down[c] in indeg:
            indeg[down[c]] += 1
    ready = sorted(c for c, d in indeg.items() if d == 0)
    order: List[int] = []
    while ready:
        c = ready.pop(0)
        order.append(c)
        d = down[c]
        if d in indeg:
            indeg[d] -= 1
            if indeg[d] == 0:
                ready.append(d)
                ready.sort()
    if len(order) != len(channels):
        cyc = sorted(set(channels) - set(order))
        raise BuildInputError(f"channel topology has a cycle involving channel(s) {cyc[:8]}")
    return order


def build_sfr(cells: pd.DataFrame, down_raw: Dict[int, int], grid: Grid, top: np.ndarray, botm: np.ndarray,
              idomain: np.ndarray, *, incision_m: float, bed_thickness_m: float, bed_k: float, mannings_n: float,
              width_m: float, widths: Optional[Dict[int, float]], min_slope: float, clearance_m: float) -> SfrBuild:
    need = ["channel_id", "seq", "layer", "row", "col", "length_m"]
    miss = [c for c in need if c not in cells.columns]
    if miss:
        raise BuildInputError(f"channel_cells: missing column(s) {miss}")
    nlay = botm.shape[0]
    lay, row, col = (cells[c].to_numpy(np.int64) - 1 for c in ("layer", "row", "col"))
    bad = (lay < 0) | (lay >= nlay) | (row < 0) | (row >= grid.nrow) | (col < 0) | (col >= grid.ncol)
    if bad.any():
        raise BuildInputError(f"channel_cells: {int(bad.sum())} row(s) outside the grid (indices are 1-based)")
    if (cells["length_m"] <= 0).any():
        raise BuildInputError("channel_cells: length_m must be positive")
    notes: List[str] = []
    if "fraction" in cells.columns:
        fs = cells.groupby("channel_id")["fraction"].sum()
        short = fs[fs < 1.0 - 1e-3]
        if len(short):
            notes.append(f"{len(short)} channel(s) have pieces outside the grid (fractions sum to less than 1; "
                         f"lowest {short.min():.3f}); fractions are not rescaled and the channel's whole local "
                         "flow is injected into its in-grid reaches")
    # a channel that leaves and re-enters a cell has several rows for (channel_id, row, col); each
    # piece becomes its own reach, ordered by seq
    active = idomain[lay, row, col] > 0
    n_drop = int((~active).sum())
    if n_drop:
        dropped_len = float(cells.loc[~active, "length_m"].sum())
        notes.append(f"{n_drop} channel piece(s) ({dropped_len:,.0f} m) lie in inactive cells and were dropped")
    df = cells.loc[active].copy()
    df["lay0"], df["row0"], df["col0"] = lay[active], row[active], col[active]
    if df.empty:
        raise BuildInputError("no channel piece lies in an active cell")
    df = df.sort_values(["channel_id", "seq"])
    channels = sorted(int(c) for c in df["channel_id"].unique())

    def present_down(c: int) -> int:
        seen = set()
        d = down_raw.get(c, 0)
        while d and d not in channels and d not in seen:
            seen.add(d)
            d = down_raw.get(d, 0)
        return d if d in channels else 0

    down = {c: present_down(c) for c in channels}
    unlinked = [c for c in channels if c not in down_raw]
    if unlinked:
        notes.append(f"{len(unlinked)} channel(s) are not in the topology and were treated as outlets "
                     f"(first: {unlinked[:5]})")
    order = _topological_order(channels, down)

    reach_rows, by_channel = [], {}
    rno = 0
    for c in order:
        g = df[df["channel_id"] == c]
        idx = []
        for k, r in enumerate(g.itertuples(index=False), start=1):
            reach_rows.append(dict(rno=rno, channel_id=c, seq=k, layer=int(r.lay0), row=int(r.row0),
                                   col=int(r.col0), length_m=float(r.length_m)))
            idx.append(rno)
            rno += 1
        by_channel[c] = idx
    R = pd.DataFrame(reach_rows)

    # streambed top: cell top - incision, never rising downstream within a channel
    rtp = top[R["row"], R["col"]] - incision_m
    for c, idx in by_channel.items():
        rtp[idx] = np.minimum.accumulate(rtp[idx])
    floor = botm[R["layer"], R["row"], R["col"]] + bed_thickness_m + clearance_m
    n_clamp = int((rtp < floor).sum())
    rtp = np.maximum(rtp, floor)
    if n_clamp:
        notes.append(f"{n_clamp} reach(es) had the streambed bottom below the cell bottom and were raised to "
                     f"{clearance_m:g} m above it")
    above = rtp > top[R["row"], R["col"]] + 1e-9
    if above.any():
        notes.append(f"{int(above.sum())} reach(es) have the streambed top above the cell top because the cell "
                     "is thinner than the streambed (check layer thickness)")
    # gradient from the bed profile with a floor
    slope = np.full(len(R), min_slope)
    for c, idx in by_channel.items():
        if len(idx) < 2:
            continue
        L = R.loc[idx, "length_m"].to_numpy()
        z = rtp[idx]
        s = np.empty(len(idx))
        s[:-1] = (z[:-1] - z[1:]) / (0.5 * (L[:-1] + L[1:]))
        s[-1] = s[-2]
        slope[idx] = np.maximum(s, min_slope)
    conns: Dict[int, List[int]] = {i: [] for i in range(len(R))}
    for c, idx in by_channel.items():
        for j, r in enumerate(idx):
            if j > 0:
                conns[r].append(idx[j - 1])
            if j < len(idx) - 1:
                conns[r].append(-idx[j + 1])
    n_links = 0
    for c in order:
        d = down[c]
        if not d:
            continue
        last = by_channel[c][-1]
        lr, lc = R.at[last, "row"], R.at[last, "col"]
        cand = by_channel[d]
        dist = [(R.at[r, "row"] - lr) ** 2 + (R.at[r, "col"] - lc) ** 2 for r in cand]
        tgt = cand[int(np.argmin(dist))]
        assert tgt > 0, "downstream link to reach 0 (topological order violated)"
        conns[last].append(-tgt)
        conns[tgt].append(last)
        n_links += 1
    pkg, con = [], []
    for i, r in R.iterrows():
        w = (widths or {}).get(int(r.channel_id), width_m)
        pkg.append([int(i), (int(r.layer), int(r.row), int(r.col)), float(r.length_m), float(w), float(slope[i]),
                    float(rtp[i]), float(bed_thickness_m), float(bed_k), float(mannings_n), len(conns[i]), 1.0, 0])
        if conns[i]:
            con.append([int(i)] + conns[i])
    out = pd.DataFrame({"rno": R["rno"] + 1, "channel_id": R["channel_id"], "seq": R["seq"],
                        "layer": R["layer"] + 1, "row": R["row"] + 1, "col": R["col"] + 1,
                        "length_m": R["length_m"]})
    return SfrBuild(pkg, con, out, notes, dict(n_reaches=len(R), n_channels=len(channels), n_links=n_links,
                                               n_dropped_pieces=n_drop, n_bed_clamped=n_clamp))
