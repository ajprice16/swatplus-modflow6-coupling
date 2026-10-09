"""Ordering and orientation of (possibly multipart) channel lines.

Assumption used by the plugin, stated so users can check it against their
data: **a channel line is digitised from its upstream end to its downstream
end** (the convention of the flow-direction-derived stream networks that
SWAT+ delineation tools produce). Two overrides exist: a ``reverse`` switch
that flips every channel, and an elevation check (a DEM sampled at both ends)
that orients each channel downhill regardless of digitising direction.

Multipart features are chained into a single path: parts are joined where an
end of one part coincides (within ``tol``) with an end of another, flipping
parts as needed. Parts that cannot be joined stay separate and are appended in
the order of their position along the dominant direction, which is reported
through the ``gaps`` count so the caller can warn.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

Point = Tuple[float, float]
Polyline = List[Point]


def _d(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def chain_parts(parts: Sequence[Sequence[Point]], tol: float = 1e-6) -> Tuple[List[Polyline], int]:
    """Chain line parts end to end.

    Returns ``(ordered_parts, gaps)`` where each element of ``ordered_parts``
    is an oriented polyline and ``gaps`` is the number of joins that were not
    exact (``gap > tol``) or missing. The first part keeps its digitised
    direction unless another part attaches at its start.
    """
    parts = [list(p) for p in parts if len(p) >= 2]
    if not parts:
        return [], 0
    if len(parts) == 1:
        return [parts[0]], 0

    remaining = parts[:]
    # Start from the part whose start point is not touched by any other part's end.
    def touches_start(p, others):
        return any(_d(p[0], o[-1]) <= tol or _d(p[0], o[0]) <= tol for o in others if o is not p)
    start = next((p for p in remaining if not touches_start(p, remaining)), remaining[0])
    remaining.remove(start)
    chain = [start]
    gaps = 0
    while remaining:
        tail = chain[-1][-1]
        best = None  # (distance, part, flip)
        for p in remaining:
            for flip in (False, True):
                head = p[-1] if flip else p[0]
                dist = _d(tail, head)
                if best is None or dist < best[0]:
                    best = (dist, p, flip)
        dist, p, flip = best
        remaining.remove(p)
        if dist > tol:
            gaps += 1
        chain.append(p[::-1] if flip else p)
    return chain, gaps


def orient(parts: Sequence[Polyline], reverse: bool) -> List[Polyline]:
    """Reverse a chain (order of parts and direction of each) when ``reverse``."""
    if not reverse:
        return [list(p) for p in parts]
    return [list(p[::-1]) for p in reversed(parts)]


def end_points(parts: Sequence[Polyline]) -> Optional[Tuple[Point, Point]]:
    """First and last vertex of a chained path."""
    if not parts:
        return None
    return parts[0][0], parts[-1][-1]


def decide_downhill(z_start: Optional[float], z_end: Optional[float], min_drop: float = 0.0) -> Optional[bool]:
    """Compare end elevations. True: digitised downhill; False: uphill (needs reversing); None: undecidable."""
    if z_start is None or z_end is None:
        return None
    if not (math.isfinite(z_start) and math.isfinite(z_end)):
        return None
    if abs(z_start - z_end) <= min_drop:
        return None
    return z_start > z_end
