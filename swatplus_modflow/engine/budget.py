"""Parse the per-time-step volumetric budget MODFLOW 6 writes to its listing file.

The model budget ("VOLUME BUDGET FOR ENTIRE MODEL") lists, for the current time
step, every flow term in and out (storage, each package) in model volume/time
units and ends with PERCENT DISCREPANCY. Because the engine runs one-day steps
with TIME_UNITS DAYS and LENGTH_UNITS METERS, the "RATES FOR THIS TIME STEP"
column is in m3/day.

Fortran writes the listing as the step finishes; the reader keeps a byte offset
and returns only complete blocks, so a block that is still being flushed is
picked up on the next poll rather than parsed half-written.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HEAD = re.compile(r"VOLUME BUDGET FOR ENTIRE MODEL AT END OF TIME STEP\s+(\d+),\s+STRESS PERIOD\s+(\d+)")
_TERM = re.compile(r"^\s*([A-Z0-9][A-Z0-9_\-]*)\s*=\s*(\S+)\s+([A-Z0-9][A-Z0-9_\-]*)\s*=\s*(\S+)\s*(\S*)\s*$")
_PCT = re.compile(r"PERCENT DISCREPANCY\s*=\s*(\S+)\s+PERCENT DISCREPANCY\s*=\s*(\S+)")
_TOT = re.compile(r"^\s*TOTAL (IN|OUT)\s*=\s*(\S+)\s+TOTAL (?:IN|OUT)\s*=\s*(\S+)")


def _num(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return float("nan")


@dataclass
class BudgetRecord:
    kper: int
    kstp: int
    terms_in: Dict[str, float] = field(default_factory=dict)    # key = package name (or term name)
    terms_out: Dict[str, float] = field(default_factory=dict)
    kinds: Dict[str, str] = field(default_factory=dict)         # key -> term type (e.g. 'RCHA', 'STO-SY')
    total_in: float = float("nan")
    total_out: float = float("nan")
    percent_discrepancy: float = float("nan")

    def net(self, key: str) -> float:
        """Net flow INTO the aquifer for the package/term ``key`` (m3/day)."""
        return self.terms_in.get(key, 0.0) - self.terms_out.get(key, 0.0)

    def keys_of_kind(self, *kinds: str) -> List[str]:
        ks = {k.upper() for k in kinds}
        return [k for k, v in self.kinds.items() if v.upper() in ks]

    def storage_in(self) -> float:
        return sum(self.terms_in.get(k, 0.0) for k in self.kinds if self.kinds[k].upper().startswith("STO"))

    def storage_out(self) -> float:
        return sum(self.terms_out.get(k, 0.0) for k in self.kinds if self.kinds[k].upper().startswith("STO"))


def parse_budget_text(text: str) -> Tuple[List[BudgetRecord], int]:
    """Return ``(complete records, number of characters consumed)``."""
    recs: List[BudgetRecord] = []
    consumed = 0
    pos = 0
    while True:
        m = _HEAD.search(text, pos)
        if not m:
            break
        pcts = list(_PCT.finditer(text, m.end()))
        if not pcts:
            break                                   # block not complete yet
        end = pcts[0].end()
        block = text[m.start():end]
        rec = BudgetRecord(kper=int(m.group(2)), kstp=int(m.group(1)))
        side = None
        for line in block.splitlines():
            s = line.strip()
            if s.startswith("IN:"):
                side = "in"
                continue
            if s.startswith("OUT:"):
                side = "out"
                continue
            tm = _TOT.match(line)
            if tm:
                v = _num(tm.group(3))
                if tm.group(1) == "IN":
                    rec.total_in = v
                else:
                    rec.total_out = v
                continue
            t = _TERM.match(line)
            if t and side:
                kind, rate, pname = t.group(3), _num(t.group(4)), t.group(5)
                key = pname if pname and pname.upper() != "STORAGE" else kind
                rec.kinds[key] = kind
                (rec.terms_in if side == "in" else rec.terms_out)[key] = rate
        pm = pcts[0]
        rec.percent_discrepancy = _num(pm.group(2))
        recs.append(rec)
        pos = end
        consumed = end
    return recs, consumed


class ListingBudgetReader:
    """Incrementally read model budgets from ``path`` (MODFLOW 6 listing file)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._offset = 0
        self._buf = ""

    def poll(self) -> List[BudgetRecord]:
        if not self.path.exists():
            return []
        with open(self.path, "rb") as fh:
            fh.seek(self._offset)
            data = fh.read()
        self._offset += len(data)
        self._buf += data.decode("utf-8", errors="replace")
        recs, used = parse_budget_text(self._buf)
        self._buf = self._buf[used:]
        # keep the buffer small: only text from the last unfinished header onward matters
        h = self._buf.rfind("VOLUME BUDGET FOR ENTIRE MODEL")
        self._buf = self._buf[h:] if h >= 0 else self._buf[-200:]
        return recs
