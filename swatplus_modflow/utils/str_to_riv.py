"""Convert a MODFLOW-2005 Stream (STR) package to MODFLOW 6 RIV records.

The STR package (Prudic, 1989) routes flow between reaches and can compute
stage from Manning's equation. MODFLOW 6 has no STR package; the usual
migration targets are SFR (full routing) or RIV (head-dependent boundary
with a prescribed stage). This module performs the RIV conversion, which is
a deliberate simplification:

* stream routing, flow limits and diversions are dropped -- a RIV cell can
  supply unlimited water to the aquifer, whereas an STR reach cannot lose
  more than the flow routed into it;
* when ``ICALC > 0`` the STR "Stage" field is only an initial value (STR
  recomputes stage each time step), so the converted RIV stage is
  approximate and a warning is issued.

Supported input: free-format STR files as documented for MODFLOW-2005
(items 2, 5, 6, 7, 8, 9), plus fixed-format item 6/7 records
(``5I5,F15.0,4F10.0`` and ``3F10.0``). Parameterized STR files (item 1
``PARAMETER``) are rejected explicitly.

Units are passed through unchanged; the caller is responsible for making
sure the STR model and the MF6 model share length and time units.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StrReach:
    """One STR reach record (cell indices are 0-based)."""

    layer: int
    row: int
    col: int
    segment: int
    reach: int
    flow: float
    stage: float
    cond: float
    sbot: float
    stop: float


@dataclass
class StrPackage:
    """Parsed contents of an STR file relevant to a RIV conversion."""

    mxacts: int
    nss: int
    ntrib: int
    ndiv: int
    icalc: int
    const: float
    # stress-period index (0-based) -> reaches active in that period.
    # Periods with ITMP < 0 hold the previous period's list (same object).
    periods: Dict[int, List[StrReach]] = field(default_factory=dict)

    @property
    def nper(self) -> int:
        return len(self.periods)


class StrFormatError(ValueError):
    """Raised when an STR file does not follow the expected layout."""


def _data_lines(path: Path) -> Iterator[Tuple[int, str]]:
    """Yield (line_number, text) for non-blank, non-comment lines."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, start=1):
            text = raw.rstrip("\n\r")
            if not text.strip() or text.lstrip().startswith("#"):
                continue
            yield lineno, text


def _parse_reach(lineno: int, text: str, fixed_format: bool) -> StrReach:
    try:
        if fixed_format:
            ints = [int(text[i * 5:(i + 1) * 5]) for i in range(5)]
            flow = float(text[25:40])
            reals = [float(text[40 + i * 10:50 + i * 10]) for i in range(4)]
        else:
            parts = text.split()
            if len(parts) < 10:
                raise StrFormatError(
                    f"line {lineno}: STR item 6 needs 10 values "
                    f"(Layer Row Column Seg Reach Flow Stage Cond Sbot Stop), "
                    f"got {len(parts)}: {text.strip()!r}"
                )
            ints = [int(p) for p in parts[:5]]
            flow = float(parts[5])
            reals = [float(p) for p in parts[6:10]]
    except ValueError as exc:
        raise StrFormatError(f"line {lineno}: cannot parse STR reach record: {exc}") from exc

    lay, row, col, seg, rch = ints
    if min(lay, row, col) < 1:
        raise StrFormatError(f"line {lineno}: layer/row/column must be >= 1, got {ints[:3]}")
    stage, cond, sbot, stop = reals
    return StrReach(lay - 1, row - 1, col - 1, seg, rch, flow, stage, cond, sbot, stop)


def parse_str_file(path: Path | str, fixed_format: bool = False,
                   nper: Optional[int] = None) -> StrPackage:
    """Parse a MODFLOW-2005 STR file.

    Parameters
    ----------
    path
        STR input file.
    fixed_format
        Read item 6/7 records with the fixed-column layout instead of
        whitespace-separated values.
    nper
        Number of stress periods to read. ``None`` reads until end of file.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"STR file not found: {path}")
    lines = _data_lines(path)

    def nxt(what: str) -> Tuple[int, str]:
        try:
            return next(lines)
        except StopIteration:
            raise StrFormatError(f"{path}: unexpected end of file while reading {what}") from None

    lineno, text = nxt("item 2 header")
    if text.split()[0].upper() == "PARAMETER":
        raise StrFormatError(f"{path}: parameterized STR files are not supported")
    head = text.split()
    if len(head) < 7:
        raise StrFormatError(
            f"line {lineno}: STR item 2 needs MXACTS NSS NTRIB NDIV ICALC CONST ISTCB1 ISTCB2"
        )
    mxacts, nss, ntrib, ndiv, icalc = (int(v) for v in head[:5])
    const = float(head[5])
    pkg = StrPackage(mxacts, nss, ntrib, ndiv, icalc, const)

    sp = 0
    while nper is None or sp < nper:
        try:
            lineno, text = next(lines)
        except StopIteration:
            if nper is not None:
                raise StrFormatError(f"{path}: only {sp} stress periods found, expected {nper}")
            break
        try:
            itmp = int(text.split()[0])
        except (ValueError, IndexError):
            raise StrFormatError(f"line {lineno}: expected STR item 5 (ITMP IRDFLG IPTFLG), got {text.strip()!r}")

        if itmp < 0:
            if sp == 0:
                raise StrFormatError(f"line {lineno}: ITMP < 0 in the first stress period")
            pkg.periods[sp] = pkg.periods[sp - 1]
        elif itmp == 0:
            pkg.periods[sp] = []
        else:
            if itmp > mxacts:
                raise StrFormatError(f"line {lineno}: ITMP={itmp} exceeds MXACTS={mxacts}")
            reaches = [_parse_reach(*nxt("item 6"), fixed_format) for _ in range(itmp)]
            if icalc > 0:  # item 7: width slope rough, one per reach
                for _ in range(itmp):
                    nxt("item 7")
            if ntrib > 0:  # item 8: one line per segment
                for _ in range(nss):
                    nxt("item 8")
            if ndiv > 0:   # item 9: one line per segment
                for _ in range(nss):
                    nxt("item 9")
            pkg.periods[sp] = reaches
        sp += 1

    if not pkg.periods:
        raise StrFormatError(f"{path}: no stress periods found")
    return pkg


def str_to_riv_spd(pkg: StrPackage) -> Dict[int, List[Tuple[Tuple[int, int, int], float, float, float]]]:
    """Build MF6 RIV ``stress_period_data`` from a parsed STR package.

    Each record is ``((layer, row, col), stage, cond, rbot)`` with
    ``rbot = Sbot``. Reaches with zero conductance are kept (they are
    inactive in STR too) so that reach counts are preserved; reaches whose
    bottom lies above the stage are an input error and raise.
    """
    if pkg.icalc > 0:
        logger.warning(
            "STR ICALC=%d: stages are computed by STR during the run; the file "
            "values used for RIV are initial estimates only.", pkg.icalc,
        )
    spd: Dict[int, List[Tuple[Tuple[int, int, int], float, float, float]]] = {}
    for per, reaches in pkg.periods.items():
        recs = []
        for r in reaches:
            if r.cond < 0:
                raise StrFormatError(f"period {per + 1}, segment {r.segment} reach {r.reach}: negative conductance")
            if r.sbot > r.stage:
                raise StrFormatError(
                    f"period {per + 1}, segment {r.segment} reach {r.reach}: "
                    f"streambed bottom {r.sbot} is above stage {r.stage}"
                )
            recs.append(((r.layer, r.row, r.col), r.stage, r.cond, r.sbot))
        spd[per] = recs
    return spd
