"""Consistency checks on grids and connection tables (no QGIS needed)."""
from __future__ import annotations

import html
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .grid import GridSpec

ERROR = "ERROR"
WARNING = "WARNING"
INFO = "INFO"


@dataclass
class Issue:
    level: str
    code: str
    message: str


@dataclass
class Report:
    title: str = "SWAT+ MODFLOW 6 input validation"
    issues: List[Issue] = field(default_factory=list)

    def add(self, level: str, code: str, message: str) -> None:
        self.issues.append(Issue(level, code, message))

    def error(self, code, message): self.add(ERROR, code, message)
    def warning(self, code, message): self.add(WARNING, code, message)
    def info(self, code, message): self.add(INFO, code, message)

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.level == ERROR]

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if i.level == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: "Report") -> None:
        self.issues.extend(other.issues)

    def to_text(self) -> str:
        lines = [self.title, "=" * len(self.title)]
        lines.append(f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)")
        lines.append("")
        for i in self.issues:
            lines.append(f"{i.level}: [{i.code}] {i.message}")
        if not self.issues:
            lines.append("No findings.")
        return "\n".join(lines) + "\n"

    def to_html(self) -> str:
        colours = {ERROR: "#b00020", WARNING: "#a65f00", INFO: "#333333"}
        rows = "".join(
            f"<tr><td style='color:{colours.get(i.level, '#333')}'><b>{i.level}</b></td>"
            f"<td>{html.escape(i.code)}</td><td>{html.escape(i.message)}</td></tr>"
            for i in self.issues)
        return (f"<html><body><h2>{html.escape(self.title)}</h2>"
                f"<p>{len(self.errors)} error(s), {len(self.warnings)} warning(s)</p>"
                f"<table border='1' cellpadding='4' cellspacing='0'>{rows}</table></body></html>")


def check_hru_table(rows: List[dict], grid: Optional[GridSpec] = None,
                    polygon_areas: Optional[Dict[int, float]] = None,
                    tol: float = 1e-6, area_tol: float = 0.01) -> Report:
    """Checks on ``hru_cells.csv`` content.

    * fractions in (0, 1]; per-HRU sum <= 1 + tol (sum < 1 is a warning:
      that water leaves the model);
    * cells inside the grid; no duplicated (hru, layer, row, col);
    * with ``polygon_areas``: ``fraction * polygon_area == area_m2`` and every
      HRU polygon appears in the table.
    """
    rep = Report("hru_cells.csv checks")
    if not rows:
        rep.error("hru-empty", "hru_cells.csv has no rows")
        return rep
    sums: Dict[int, float] = defaultdict(float)
    areas: Dict[int, float] = defaultdict(float)
    seen = set()
    for r in rows:
        key = (r["hru_id"], r["layer"], r["row"], r["col"])
        if key in seen:
            rep.error("hru-duplicate", f"HRU {r['hru_id']} appears twice in cell layer/row/col {key[1:]}")
        seen.add(key)
        if not (0.0 < r["fraction"] <= 1.0 + tol):
            rep.error("hru-fraction-range", f"HRU {r['hru_id']} cell ({r['row']},{r['col']}): fraction {r['fraction']} not in (0, 1]")
        if r["area_m2"] <= 0:
            rep.error("hru-area", f"HRU {r['hru_id']} cell ({r['row']},{r['col']}): non-positive area_m2")
        if grid is not None and not (1 <= r["row"] <= grid.nrow and 1 <= r["col"] <= grid.ncol):
            rep.error("hru-outside-grid", f"HRU {r['hru_id']}: cell ({r['row']},{r['col']}) is outside the {grid.nrow} x {grid.ncol} grid")
        if r["layer"] < 1:
            rep.error("hru-layer", f"HRU {r['hru_id']}: layer {r['layer']} < 1")
        sums[r["hru_id"]] += r["fraction"]
        areas[r["hru_id"]] += r["area_m2"]
    for hid, s in sorted(sums.items()):
        if s > 1.0 + 1e-4:
            rep.error("hru-fraction-sum", f"HRU {hid}: fractions sum to {s:.6f} (> 1)")
        elif s < 1.0 - 1e-4:
            rep.warning("hru-partial", f"HRU {hid}: fractions sum to {s:.6f}; {100 * (1 - s):.2f}% of its area lies outside the grid and its recharge leaves the model")
    if polygon_areas is not None:
        for hid, a in sorted(polygon_areas.items()):
            if hid not in sums:
                rep.warning("hru-uncovered", f"HRU {hid} (polygon area {a:.1f} m2) has no cells in the table")
                continue
            if a > 0 and abs(areas[hid] - sums[hid] * a) > area_tol * a + 1e-6:
                rep.error("hru-area-fraction", f"HRU {hid}: sum(area_m2) = {areas[hid]:.3f} but fraction sum x polygon area = {sums[hid] * a:.3f}")
        for hid in sorted(set(sums) - set(polygon_areas)):
            rep.error("hru-unknown-id", f"HRU {hid} is in the table but not in the HRU layer")
    return rep


def check_channel_table(rows: List[dict], grid: Optional[GridSpec] = None,
                        total_lengths: Optional[Dict[int, float]] = None,
                        tol: float = 1e-4) -> Report:
    """Checks on ``channel_cells.csv``: seq is 1..n per channel, cells inside the grid,
    fractions consistent with ``length_m``, per-channel sum <= 1 (< 1 is a warning)."""
    rep = Report("channel_cells.csv checks")
    if not rows:
        rep.error("chan-empty", "channel_cells.csv has no rows")
        return rep
    by_ch: Dict[int, List[dict]] = defaultdict(list)
    for r in rows:
        by_ch[r["channel_id"]].append(r)
        if grid is not None and not (1 <= r["row"] <= grid.nrow and 1 <= r["col"] <= grid.ncol):
            rep.error("chan-outside-grid", f"channel {r['channel_id']} seq {r['seq']}: cell ({r['row']},{r['col']}) is outside the grid")
        if r["length_m"] <= 0:
            rep.error("chan-length", f"channel {r['channel_id']} seq {r['seq']}: non-positive length_m")
        if r["layer"] < 1:
            rep.error("chan-layer", f"channel {r['channel_id']} seq {r['seq']}: layer < 1")
    for cid, rs in sorted(by_ch.items()):
        seqs = [r["seq"] for r in rs]
        if sorted(seqs) != list(range(1, len(rs) + 1)):
            rep.error("chan-seq", f"channel {cid}: seq values are not 1..{len(rs)} without gaps or repeats")
        elif seqs != sorted(seqs):
            rep.warning("chan-seq-order", f"channel {cid}: rows are not listed in seq order")
        s = sum(r["fraction"] for r in rs)
        if s > 1.0 + tol:
            rep.error("chan-fraction-sum", f"channel {cid}: fractions sum to {s:.6f} (> 1)")
        elif s < 1.0 - tol:
            rep.warning("chan-partial", f"channel {cid}: fractions sum to {s:.6f}; {100 * (1 - s):.2f}% of its length lies outside the grid")
        length = sum(r["length_m"] for r in rs)
        if total_lengths is not None and cid in total_lengths:
            tl = total_lengths[cid]
            if tl > 0 and abs(length - s * tl) > 1e-3 * tl + 1e-6:
                rep.error("chan-length-fraction", f"channel {cid}: sum(length_m) = {length:.3f} but fraction sum x channel length = {s * tl:.3f}")
    if total_lengths is not None:
        for cid in sorted(set(total_lengths) - set(by_ch)):
            rep.warning("chan-uncovered", f"channel {cid} has no cells in the table (entirely outside the grid?)")
        for cid in sorted(set(by_ch) - set(total_lengths)):
            rep.error("chan-unknown-id", f"channel {cid} is in the table but not in the channel layer")
    return rep
