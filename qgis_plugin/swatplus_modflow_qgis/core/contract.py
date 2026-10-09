"""Readers and writers for the connection tables in docs/DATA_CONTRACTS.md."""
from __future__ import annotations

import csv
from typing import Dict, Iterable, List

HRU_CELLS_FORMAT = "swatplus-modflow-hru-cells/1"
CHANNEL_CELLS_FORMAT = "swatplus-modflow-channel-cells/1"

HRU_CELLS_COLUMNS = ["hru_id", "layer", "row", "col", "area_m2", "fraction"]
CHANNEL_CELLS_COLUMNS = ["channel_id", "seq", "layer", "row", "col", "length_m", "fraction"]

_HRU_TYPES = {"hru_id": int, "layer": int, "row": int, "col": int, "area_m2": float, "fraction": float}
_CH_TYPES = {"channel_id": int, "seq": int, "layer": int, "row": int, "col": int,
             "length_m": float, "fraction": float}


class ContractError(ValueError):
    """A connection table does not follow the contract."""


def _fmt(v):
    return repr(float(v)) if isinstance(v, float) else str(v)


def _write(path, columns: List[str], rows: Iterable[dict]) -> int:
    n = 0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(columns)
        for r in rows:
            w.writerow([_fmt(r[c]) for c in columns])
            n += 1
    return n


def write_hru_cells(path, rows: Iterable[dict]) -> int:
    return _write(path, HRU_CELLS_COLUMNS, rows)


def write_channel_cells(path, rows: Iterable[dict]) -> int:
    return _write(path, CHANNEL_CELLS_COLUMNS, rows)


def _read(path, types: Dict[str, type], what: str) -> List[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rdr = csv.DictReader(fh)
        missing = [c for c in types if c not in (rdr.fieldnames or [])]
        if missing:
            raise ContractError(f"{what}: missing column(s) {', '.join(missing)}")
        out = []
        for i, rec in enumerate(rdr, start=2):
            try:
                out.append({c: t(rec[c]) for c, t in types.items()})
            except (TypeError, ValueError) as exc:
                raise ContractError(f"{what}: line {i}: {exc}") from exc
        return out


def read_hru_cells(path) -> List[dict]:
    return _read(path, _HRU_TYPES, "hru_cells.csv")


def read_channel_cells(path) -> List[dict]:
    return _read(path, _CH_TYPES, "channel_cells.csv")
