"""Build, check and write the engine project file (schema_version 1).

The engine owns the schema (``swatplus_modflow/project.py``). This module
mirrors only the required structure so the plugin can give early feedback
without importing the engine; the engine's own ``validate`` remains the
authority. The file is written as JSON, which the engine's YAML loader accepts.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1
_CRS_RE = re.compile(r"^[A-Za-z]+:[0-9A-Za-z.]+$")

_RECHARGE_UPDATE = ("daily", "monthly", "monthly_ramped")


def relativize(path: Optional[str], base_dir: str) -> Optional[str]:
    """Return ``path`` relative to ``base_dir`` when it lies inside it, else unchanged.

    Relative paths in the project file are resolved by the engine against the
    project file's folder, so keeping paths relative makes the folder portable.
    """
    if not path:
        return path
    try:
        if not os.path.isabs(path):
            return path.replace("\\", "/")
        rel = os.path.relpath(path, base_dir)
    except ValueError:  # different drive on Windows
        return path
    if rel.startswith(os.pardir):
        return path
    return rel.replace("\\", "/")


def _iso(d) -> str:
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


def build_project_dict(*, name: str, crs: str, txtinout: str, sim_dir: str, libmf6: str,
                       hru_cells: str, channel_reaches: str, start, end,
                       executable: Optional[str] = None, run_swatplus: bool = True,
                       model_name: Optional[str] = None, initial_heads: Optional[str] = None,
                       well_cells: Optional[str] = None, spinup_days: int = 0,
                       recharge_update: Optional[str] = None, recharge_delay_days: Optional[float] = None,
                       pumping_scale: Optional[float] = None, recharge_to_water_table: Optional[bool] = None,
                       et_extinction_depth_m: Optional[float] = None,
                       transmission_loss_k_mm_per_h: Optional[float] = None,
                       transmission_loss_duration_h: Optional[float] = None,
                       mass_balance_tolerance_percent: Optional[float] = None,
                       enforce_invariants: Optional[bool] = None,
                       output_directory: Optional[str] = None, output_prefix: Optional[str] = None,
                       base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Assemble the project mapping. Optional values left as None are omitted so the
    engine's defaults apply. Paths are made relative to ``base_dir`` when inside it."""
    def rp(p):
        return relativize(p, base_dir) if base_dir else p

    swat: Dict[str, Any] = {"txtinout": rp(txtinout), "run_swatplus": bool(run_swatplus)}
    if executable:
        swat["executable"] = executable
    mf: Dict[str, Any] = {"sim_dir": rp(sim_dir), "libmf6": rp(libmf6)}
    if model_name:
        mf["model_name"] = model_name
    if initial_heads:
        mf["initial_heads"] = rp(initial_heads)
    conn: Dict[str, Any] = {"hru_cells": rp(hru_cells), "channel_reaches": rp(channel_reaches)}
    if well_cells:
        conn["well_cells"] = rp(well_cells)

    coupling: Dict[str, Any] = {}
    for key, val in (("recharge_update", recharge_update), ("recharge_delay_days", recharge_delay_days),
                     ("pumping_scale", pumping_scale), ("recharge_to_water_table", recharge_to_water_table),
                     ("et_extinction_depth_m", et_extinction_depth_m)):
        if val is not None:
            coupling[key] = val
    if transmission_loss_k_mm_per_h is not None:
        tl: Dict[str, Any] = {"k_mm_per_h": transmission_loss_k_mm_per_h}
        if transmission_loss_duration_h is not None:
            tl["duration_h"] = transmission_loss_duration_h
        coupling["transmission_loss"] = tl
    checks: Dict[str, Any] = {}
    if mass_balance_tolerance_percent is not None:
        checks["mass_balance_tolerance_percent"] = mass_balance_tolerance_percent
    if enforce_invariants is not None:
        checks["enforce_invariants"] = enforce_invariants
    output: Dict[str, Any] = {}
    if output_directory:
        output["directory"] = rp(output_directory)
    if output_prefix is not None:
        output["prefix"] = output_prefix

    data: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "crs": crs,
        "swatplus": swat,
        "modflow": mf,
        "connections": conn,
        "period": {"start": _iso(start), "end": _iso(end), "spinup_days": int(spinup_days)},
    }
    if coupling:
        data["coupling"] = coupling
    if checks:
        data["checks"] = checks
    if output:
        data["output"] = output
    return data


def check_project_dict(data: Dict[str, Any]) -> List[str]:
    """Structural problems detectable without the engine (empty list: none found)."""
    problems: List[str] = []
    if not isinstance(data, dict):
        return ["project must be a mapping"]
    if data.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")
    if not str(data.get("name", "")).strip():
        problems.append("name is empty")
    crs = str(data.get("crs", ""))
    if not _CRS_RE.match(crs):
        problems.append(f"crs must look like 'AUTHORITY:CODE', got {crs!r}")
    for section, keys in (("swatplus", ("txtinout",)), ("modflow", ("sim_dir", "libmf6")),
                          ("connections", ("hru_cells", "channel_reaches")), ("period", ("start", "end"))):
        sec = data.get(section)
        if not isinstance(sec, dict):
            problems.append(f"section '{section}' is missing")
            continue
        for k in keys:
            if not sec.get(k):
                problems.append(f"{section}.{k} is required")
    swat = data.get("swatplus") or {}
    if isinstance(swat, dict) and swat.get("run_swatplus", True) and not swat.get("executable"):
        problems.append("swatplus.executable is required when swatplus.run_swatplus is true")
    per = data.get("period") or {}
    try:
        s, e = date.fromisoformat(str(per.get("start"))), date.fromisoformat(str(per.get("end")))
        if e <= s:
            problems.append(f"period.end ({e}) must be after period.start ({s})")
    except ValueError:
        problems.append("period.start and period.end must be ISO dates (YYYY-MM-DD)")
    ru = (data.get("coupling") or {}).get("recharge_update")
    if ru is not None and ru not in _RECHARGE_UPDATE:
        problems.append(f"coupling.recharge_update must be one of {', '.join(_RECHARGE_UPDATE)}")
    return problems


def write_project_json(data: Dict[str, Any], path) -> None:
    """Write the project mapping as (YAML-compatible) JSON."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def read_project_json(path) -> Dict[str, Any]:
    """Read a project file written by this plugin (JSON). Hand-written YAML is not parsed
    here, because the plugin must not depend on PyYAML; such files can be run as they are."""
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not JSON (the plugin cannot edit YAML files; it can still run them): {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data
