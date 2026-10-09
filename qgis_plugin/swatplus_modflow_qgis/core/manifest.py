"""Reading ``run_manifest.json`` and cell-value tables (docs/DATA_CONTRACTS.md, section 6)."""
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

MANIFEST_FORMAT = "swatplus-modflow-run/1"


class ManifestError(ValueError):
    pass


@dataclass
class ManifestFile:
    kind: str
    name: str
    path: str            # absolute
    description: str = ""
    units: str = ""


@dataclass
class Manifest:
    project: str
    status: str
    started: str
    finished: str
    folder: str
    files: List[ManifestFile]


def read_manifest(path) -> Manifest:
    path = os.path.abspath(path)
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    fmt = data.get("format", "")
    if fmt.split("/")[0] != MANIFEST_FORMAT.split("/")[0] or fmt.split("/")[-1] != MANIFEST_FORMAT.split("/")[-1]:
        raise ManifestError(f"unsupported manifest format {fmt!r} (this plugin reads {MANIFEST_FORMAT})")
    folder = os.path.dirname(path)
    files = []
    for entry in data.get("files", []):
        kind, name, rel = entry.get("kind"), entry.get("name"), entry.get("path")
        if kind not in ("table", "cell_values") or not name or not rel:
            raise ManifestError(f"malformed manifest entry: {entry!r}")
        files.append(ManifestFile(kind, name, os.path.normpath(os.path.join(folder, rel)),
                                  entry.get("description", ""), entry.get("units", "")))
    return Manifest(str(data.get("project", "")), str(data.get("status", "")),
                    str(data.get("started", "")), str(data.get("finished", "")), folder, files)


def read_cell_values(path) -> Tuple[List[dict], bool]:
    """Rows of a ``cell_values`` CSV and whether it has a ``date`` column.

    Each row: layer, row, col (int), value (float), date (str or None).
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rdr = csv.DictReader(fh)
        names = rdr.fieldnames or []
        missing = [c for c in ("layer", "row", "col", "value") if c not in names]
        if missing:
            raise ManifestError(f"{os.path.basename(path)}: missing column(s) {', '.join(missing)}")
        has_date = "date" in names
        rows = []
        for i, rec in enumerate(rdr, start=2):
            try:
                rows.append({"layer": int(rec["layer"]), "row": int(rec["row"]), "col": int(rec["col"]),
                             "value": float(rec["value"]) if rec["value"] not in ("", None) else None,
                             "date": rec["date"] if has_date else None})
            except ValueError as exc:
                raise ManifestError(f"{os.path.basename(path)}: line {i}: {exc}") from exc
    return rows, has_date


def latest_by_layer(rows: List[dict]) -> Tuple[Dict[int, Dict[Tuple[int, int], Optional[float]]], Optional[str]]:
    """Pick, per MODFLOW layer, the values of the latest date (all rows when there is no date).

    Returns ``({layer: {(row, col): value}}, latest_date_or_None)``. ISO dates sort lexically.
    """
    dates = [r["date"] for r in rows if r["date"]]
    latest = max(dates) if dates else None
    out: Dict[int, Dict[Tuple[int, int], Optional[float]]] = {}
    for r in rows:
        if latest is not None and r["date"] != latest:
            continue
        out.setdefault(r["layer"], {})[(r["row"], r["col"])] = r["value"]
    return out, latest
