"""Aquifer-property zones with EXPLICIT precedence.

Why this module exists: an earlier research builder drew zone categories in a fixed
order and a large regional class drawn last covered every cell, so all finer zones
were silently overwritten and K came out uniform. Here

* each zone carries a unique integer ``priority``; the larger one wins an overlap
  (zones are applied from the lowest to the highest priority),
* every property is resolved separately, using only the zones that set that
  property (a zone that sets only ``sy`` never erases another zone's ``k``),
* the result reports, per zone and property, how many cells its geometry
  covered, how many it actually set, how many it lost and to which zones, and how
  many cells it took over from lower-priority zones,
* a zone that takes over cells from other zones and covers more than
  ``max_zone_share_percent`` of the active cells is flagged, because that
  is the signature of the overwrite bug.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

import numpy as np


@dataclass
class ZoneInput:
    name: str
    priority: int
    mask: np.ndarray            # bool (nlay, nrow, ncol): where the zone's geometry applies


@dataclass
class ZoneStat:
    zone: str
    prop: str
    priority: int
    cells_in_geometry: int      # active cell-layers the zone's geometry covers
    cells_set: int              # cell-layers where the zone's value is in the final array
    cells_lost: int             # covered but won by a higher-priority zone
    cells_took_over: int        # cell-layers that held a lower-priority zone's value before this zone
    lost_to: Dict[str, int] = field(default_factory=dict)
    share_of_active_percent: float = 0.0


@dataclass
class Resolution:
    label: np.ndarray           # int16 (nlay, nrow, ncol): index into the input list, -1 = default
    stats: List[ZoneStat]
    dominant: List[str]         # human-readable problems


def resolve_property(prop: str, zones: Sequence[ZoneInput], active: np.ndarray,
                     max_zone_share_percent: float) -> Resolution:
    """Resolve which zone supplies ``prop`` in each cell (lowest priority applied first)."""
    if len({z.priority for z in zones}) != len(zones):
        raise ValueError("zone priorities must be unique")
    label = np.full(active.shape, -1, dtype=np.int16)
    took = {}
    for idx in sorted(range(len(zones)), key=lambda i: zones[i].priority):
        m = zones[idx].mask & active
        took[idx] = int(((label >= 0) & m).sum())
        label[m] = idx
    n_active = int(active.sum())
    stats, problems = [], []
    for idx, z in enumerate(zones):
        m = z.mask & active
        n_geo = int(m.sum())
        n_set = int((label == idx).sum())
        lost_mask = m & (label != idx)
        lost_to: Dict[str, int] = {}
        if lost_mask.any():
            vals, counts = np.unique(label[lost_mask], return_counts=True)
            lost_to = {zones[int(v)].name: int(c) for v, c in zip(vals, counts)}
        share = 100.0 * n_geo / n_active if n_active else 0.0
        stats.append(ZoneStat(z.name, prop, z.priority, n_geo, n_set, n_geo - n_set, took[idx], lost_to, share))
        if took[idx] > 0 and share > max_zone_share_percent:
            problems.append(
                f"zone '{z.name}' (priority {z.priority}) sets '{prop}' over {share:.1f}% of the active cells "
                f"(limit {max_zone_share_percent:g}%) and takes over {took[idx]} cell(s) from lower-priority "
                "zones; finer zones may be overwritten. Lower its priority or narrow its geometry")
    return Resolution(label, stats, problems)


def apply_values(default: float, zones: Sequence[ZoneInput], values: Sequence[float],
                 resolution: Resolution) -> np.ndarray:
    """Property array: ``default`` where no zone applies, else the winning zone's value."""
    out = np.full(resolution.label.shape, float(default))
    for idx, v in enumerate(values):
        out[resolution.label == idx] = float(v)
    return out
