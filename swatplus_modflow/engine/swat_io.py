"""Readers for the SWAT+ input and output files the daily coupler needs.

SWAT+ text outputs have three header lines (title, column names, units)
followed by one row per object per day. They are written either whitespace
delimited (``*.txt``) or comma delimited (``*.csv``). Columns are located by
the names in the header line, never by position, so a different SWAT+
revision that reorders columns still reads correctly.

Object ids: the ``unit`` column of an output row is the object number, i.e.
the ``id`` column of the matching ``*.con`` file, which is what
``hru_cells.csv`` (``hru_id``) and ``sfr_reaches.csv`` (``channel_id``)
use. Files without a ``unit`` column fall back to ``gis_id``.

Units are read from the units line when it lines up with the header; a
unit this module does not recognise is an error, never a guess.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .errors import EngineInputError

log = logging.getLogger(__name__)

# multiplier to the engine's working unit
_UNITS_PERC_MM = {"mm": 1.0, "mm/day": 1.0}
_UNITS_FLOW_M3S = {"m^3/s": 1.0, "m3/s": 1.0, "cms": 1.0}
_UNITS_VOL_M3 = {"m^3": 1.0, "m3": 1.0, "ha-m": 1.0e4, "ha_m": 1.0e4, "ham": 1.0e4}

_CHUNK_ROWS = 2_000_000


@dataclass(frozen=True)
class TableHeader:
    path: Path
    names: Tuple[str, ...]
    units: Tuple[str, ...]            # empty when the units line does not align with the names
    delimiter: Optional[str]          # None = whitespace
    data_start: int                   # 0-based line index of the first data row

    def index(self, name: str) -> int:
        try:
            return self.names.index(name)
        except ValueError:
            raise EngineInputError(
                f"{self.path.name}: column '{name}' not found; header has {list(self.names)}") from None

    def has(self, name: str) -> bool:
        return name in self.names

    def unit_of(self, name: str) -> Optional[str]:
        return self.units[self.index(name)].lower() if self.units else None


def find_output(txtinout: Path, stem: str) -> Optional[Path]:
    """Return ``<stem>.txt`` or ``<stem>.csv`` in ``txtinout`` (``.txt`` preferred), else None."""
    for ext in (".txt", ".csv"):
        p = Path(txtinout) / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def read_header(path: Path) -> TableHeader:
    path = Path(path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = [fh.readline() for _ in range(3)]
    if not lines[1].strip():
        raise EngineInputError(f"{path.name}: no column-name line (expected title, names, units, data)")
    delim = "," if "," in lines[1] else None
    split = (lambda s: [t.strip() for t in s.strip().split(",")]) if delim else (lambda s: s.split())
    names = tuple(n.lower() for n in split(lines[1]))
    units = tuple(split(lines[2]))
    if len(units) < len(names) and len(names) - len(units) <= 8:
        # whitespace-delimited files leave the unit of the leading id/name columns blank, so the
        # units line is shorter than the header; its entries belong to the LAST columns
        units = ("",) * (len(names) - len(units)) + units
    if len(units) != len(names):
        units = ()
    return TableHeader(path, names, units, delim, 3)


def _factor(hdr: TableHeader, col: str, table: Dict[str, float], assumed: str) -> float:
    unit = hdr.unit_of(col)
    if unit is None or unit in ("", "-"):
        log.warning("%s: no unit given for '%s' in the units line; assuming %s",
                    hdr.path.name, col, assumed)
        return 1.0
    if unit not in table:
        raise EngineInputError(
            f"{hdr.path.name}: column '{col}' has unit '{unit}', which this engine does not convert "
            f"(supported: {sorted(table)}). Refusing to guess.")
    return table[unit]


def _date_key(yr, mon, day):
    return (np.asarray(yr, dtype=np.int64) * 10000 + np.asarray(mon, dtype=np.int64) * 100
            + np.asarray(day, dtype=np.int64))


def date_keys(start: date, end: date) -> np.ndarray:
    """Sorted yyyymmdd integers for every day in [start, end]."""
    n = (end - start).days + 1
    days = [start + timedelta(days=i) for i in range(n)]
    return np.array([d.year * 10000 + d.month * 100 + d.day for d in days], dtype=np.int64)


def output_date_range(path: Path) -> Tuple[date, date]:
    """First and last date of a SWAT+ daily output file without reading the whole file."""
    hdr = read_header(path)
    iy, im, idd = hdr.index("yr"), hdr.index("mon"), hdr.index("day")

    def parse(line: str) -> date:
        tok = [t.strip() for t in line.split(",")] if hdr.delimiter else line.split()
        return date(int(float(tok[iy])), int(float(tok[im])), int(float(tok[idd])))

    with open(path, "rb") as fh:
        for _ in range(hdr.data_start):
            fh.readline()
        first = fh.readline().decode("utf-8", "replace")
        fh.seek(0, 2)
        size = fh.tell()
        back = min(size, 65536)
        fh.seek(size - back)
        tail = fh.read().decode("utf-8", "replace").strip().splitlines()
    if not first.strip() or not tail:
        raise EngineInputError(f"{Path(path).name}: no data rows")
    return parse(first), parse(tail[-1])


def read_daily_matrix(path: Path, value_cols: Sequence[str], ids: Sequence[int],
                      start: date, end: date, *, id_cols: Sequence[str] = ("unit", "gis_id"),
                      factors: Optional[Dict[str, Dict[str, float]]] = None,
                      assumed_units: Optional[Dict[str, str]] = None,
                      optional: Iterable[str] = ()) -> Tuple[Dict[str, np.ndarray], List[int]]:
    """Read selected columns for selected object ids into ``(n_days, n_ids)`` float64 arrays.

    ``ids`` are object numbers; rows for other objects or other dates are skipped.
    A (day, id) cell that no row filled is NaN, so the caller can detect gaps.
    Duplicate rows for the same (day, id) are an error.
    """
    hdr = read_header(path)
    optional = set(optional)
    id_col = next((c for c in id_cols if hdr.has(c)), None)
    if id_col is None:
        raise EngineInputError(f"{Path(path).name}: none of the id columns {list(id_cols)} present")
    missing = [c for c in value_cols if not hdr.has(c) and c not in optional]
    if missing:
        raise EngineInputError(f"{Path(path).name}: required column(s) {missing} not found; "
                               f"header has {list(hdr.names)}")
    present = [c for c in value_cols if hdr.has(c)]
    mult = {}
    for c in present:
        table, assumed = (factors or {}).get(c), (assumed_units or {}).get(c, "its documented unit")
        mult[c] = _factor(hdr, c, table, assumed) if table else 1.0
    cols = ["yr", "mon", "day", id_col] + present
    pos = [hdr.index(c) for c in cols]
    order = np.argsort(pos)
    ids_arr = np.asarray(sorted(set(int(i) for i in ids)), dtype=np.int64)
    id_to_j = {int(v): j for j, v in enumerate(ids_arr)}
    keys = date_keys(start, end)
    out = {c: np.full((len(keys), len(ids_arr)), np.nan, dtype=np.float64) for c in present}
    seen = np.zeros((len(keys), len(ids_arr)), dtype=bool)
    reader = pd.read_csv(
        path, sep=hdr.delimiter if hdr.delimiter else r"\s+", header=None, skiprows=hdr.data_start,
        usecols=[pos[i] for i in order], engine="c", chunksize=_CHUNK_ROWS,
        dtype=np.float64, na_values=["NaN", "nan", "*", "********", "Infinity", "-Infinity", "inf", "-inf"])
    sorted_cols = [cols[i] for i in order]
    for chunk in reader:
        chunk.columns = sorted_cols
        k = _date_key(chunk["yr"].to_numpy(), chunk["mon"].to_numpy(), chunk["day"].to_numpy())
        di = np.searchsorted(keys, k)
        ok_d = (di < len(keys)) & (keys[np.minimum(di, len(keys) - 1)] == k)
        oid = chunk[id_col].to_numpy().astype(np.int64)
        ji = np.searchsorted(ids_arr, oid)
        ok_i = (ji < len(ids_arr)) & (ids_arr[np.minimum(ji, len(ids_arr) - 1)] == oid)
        sel = ok_d & ok_i
        if not sel.any():
            continue
        d_sel, j_sel = di[sel], ji[sel]
        if seen[d_sel, j_sel].any():
            raise EngineInputError(f"{Path(path).name}: duplicate rows for the same day and object")
        flat = np.unique(d_sel * len(ids_arr) + j_sel)
        if len(flat) != len(d_sel):
            raise EngineInputError(f"{Path(path).name}: duplicate rows for the same day and object")
        seen[d_sel, j_sel] = True
        for c in present:
            out[c][d_sel, j_sel] = chunk[c].to_numpy()[sel] * mult[c]
    return out, [int(v) for v in ids_arr]


# --------------------------------------------------------------------------- inputs

def _read_con(path: Path) -> Tuple[List[str], List[List[str]]]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    if len(lines) < 2:
        raise EngineInputError(f"{Path(path).name}: empty")
    names = [n.lower() for n in lines[1].split()]
    rows = [ln.split() for ln in lines[2:] if ln.strip()]
    return names, rows


def read_hru_areas(txtinout: Path) -> Dict[int, float]:
    """``hru.con`` areas in m^2 (the file stores hectares), keyed by the ``id`` column."""
    path = Path(txtinout) / "hru.con"
    if not path.exists():
        raise EngineInputError(f"hru.con not found in {txtinout}")
    names, rows = _read_con(path)
    if "id" not in names or "area" not in names:
        raise EngineInputError(f"hru.con: needs 'id' and 'area' columns, header is {names}")
    i_id, i_ar = names.index("id"), names.index("area")
    out = {int(r[i_id]): float(r[i_ar]) * 1.0e4 for r in rows}
    if not out:
        raise EngineInputError("hru.con lists no HRUs")
    return out


def read_connection_topology(txtinout: Path) -> Tuple[Dict[int, List[int]], List[int]]:
    """Channel network from ``chandeg.con`` and ``reservoir.con`` (outflow connections).

    Returns ``(upstream_of, channel_ids)``: ``upstream_of[c]`` lists the objects that
    flow directly into channel ``c``. Reservoirs are kept as graph nodes (keyed by
    ``-id`` so they cannot collide with channel ids) so that the routing through a
    reservoir is not lost; callers resolve them with :func:`upstream_with_output`.
    """
    txtinout = Path(txtinout)
    chan_path = txtinout / "chandeg.con"
    if not chan_path.exists():
        raise EngineInputError(f"chandeg.con not found in {txtinout}")
    up: Dict[int, List[int]] = {}
    channel_ids: List[int] = []

    def scan(path: Path, own_sign: int, record_ids: bool):
        names, rows = _read_con(path)
        if "id" not in names or "out_tot" not in names:
            raise EngineInputError(f"{path.name}: needs 'id' and 'out_tot' columns, header is {names}")
        i_id, i_out = names.index("id"), names.index("out_tot")
        for r in rows:
            oid = own_sign * int(r[i_id])
            if record_ids:
                channel_ids.append(int(r[i_id]))
            n_out = int(float(r[i_out]))
            tok = r[i_out + 1:]
            if len(tok) < 4 * n_out:
                raise EngineInputError(
                    f"{path.name}: object {r[i_id]} declares {n_out} outflow connections but lists "
                    f"{len(tok) // 4}")
            for k in range(n_out):
                typ, did = tok[4 * k].lower(), int(float(tok[4 * k + 1]))
                if typ in ("sdc", "cha"):
                    up.setdefault(did, []).append(oid)
                elif typ == "res":
                    up.setdefault(-did, []).append(oid)

    scan(chan_path, 1, True)
    res_path = txtinout / "reservoir.con"
    if res_path.exists():
        scan(res_path, -1, False)
    return up, channel_ids


def upstream_with_output(chan_id: int, upstream_of: Dict[int, List[int]], present) -> List[int]:
    """Nearest upstream channels of ``chan_id`` that have SWAT+ channel output.

    A node without output (a reservoir, or a channel replaced by one) is replaced,
    recursively, by its own upstream nodes. The channel below a reservoir then gets
    ``flo_out - sum(flo_out of the channels feeding the reservoir)``, so the
    reservoir's storage change and evaporation appear there as net local flow.
    """
    out, stack, seen = [], list(upstream_of.get(chan_id, [])), set()
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        if u > 0 and u in present:
            out.append(u)
        else:
            stack.extend(upstream_of.get(u, []))
    return out


def read_channel_geometry(txtinout: Path) -> Dict[int, Tuple[float, float]]:
    """``{channel id: (length_km, width_m)}`` via chandeg.con -> channel-lte.cha -> hyd-sed-lte.cha."""
    txtinout = Path(txtinout)
    for f in ("chandeg.con", "channel-lte.cha", "hyd-sed-lte.cha"):
        if not (txtinout / f).exists():
            raise EngineInputError(f"{f} not found in {txtinout} (needed for transmission loss)")

    def table(f):
        names, rows = _read_con(txtinout / f)
        return names, rows

    hn, hrows = table("hyd-sed-lte.cha")
    for c in ("name", "wd", "len"):
        if c not in hn:
            raise EngineInputError(f"hyd-sed-lte.cha: column '{c}' missing, header is {hn}")
    hyd = {r[hn.index("name")]: (float(r[hn.index("len")]), float(r[hn.index("wd")])) for r in hrows}
    ln, lrows = table("channel-lte.cha")
    for c in ("id", "cha_hyd"):
        if c not in ln:
            raise EngineInputError(f"channel-lte.cha: column '{c}' missing, header is {ln}")
    lte = {int(r[ln.index("id")]): r[ln.index("cha_hyd")] for r in lrows}
    cn, crows = table("chandeg.con")
    for c in ("id", "lcha"):
        if c not in cn:
            raise EngineInputError(f"chandeg.con: column '{c}' missing, header is {cn}")
    out = {}
    for r in crows:
        cid, lcha = int(r[cn.index("id")]), int(r[cn.index("lcha")])
        if lcha not in lte or lte[lcha] not in hyd:
            raise EngineInputError(f"channel {cid}: geometry linkage incomplete (lcha {lcha})")
        out[cid] = hyd[lte[lcha]]
    return out


def read_time_sim(txtinout: Path) -> Optional[Tuple[date, date]]:
    """Simulation period from ``time.sim`` (day_start, yrc_start, day_end, yrc_end), or None."""
    path = Path(txtinout) / "time.sim"
    if not path.exists():
        return None
    lines = path.read_text(errors="replace").splitlines()
    if len(lines) < 3:
        return None
    names = [n.lower() for n in lines[1].split()]
    vals = lines[2].split()
    try:
        d0, y0 = int(vals[names.index("day_start")]), int(vals[names.index("yrc_start")])
        d1, y1 = int(vals[names.index("day_end")]), int(vals[names.index("yrc_end")])
    except (ValueError, IndexError):
        return None
    first = date(y0, 1, 1) + timedelta(days=max(d0, 1) - 1)
    last = date(y1, 12, 31) if d1 <= 0 else date(y1, 1, 1) + timedelta(days=d1 - 1)
    return first, last


UNIT_TABLES = dict(perc=_UNITS_PERC_MM, flo_out=_UNITS_FLOW_M3S, seep=_UNITS_VOL_M3)
