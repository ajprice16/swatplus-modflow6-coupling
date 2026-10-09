"""``validate``: check a project file and everything it points at without running anything."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import List, Tuple

from pydantic import ValidationError

from ..project import ProjectConfig, load_project
from . import connections_io as cio
from . import mf6_prepare, swat_io
from .driver import sim_window
from .errors import EngineInputError


def _fmt_validation(exc: ValidationError) -> List[str]:
    out = []
    for e in exc.errors():
        loc = ".".join(str(p) for p in e["loc"]) or "<project>"
        out.append(f"{loc}: {e['msg']}")
    return out


def validate_project_file(path: Path | str) -> Tuple[List[str], List[str], "ProjectConfig | None"]:
    """Return ``(errors, warnings, config or None)``."""
    errors: List[str] = []
    warnings: List[str] = []
    try:
        cfg = load_project(path)
    except FileNotFoundError:
        return [f"project file not found: {path}"], warnings, None
    except ValidationError as exc:
        return _fmt_validation(exc), warnings, None
    except (ValueError, OSError) as exc:
        return [str(exc)], warnings, None
    e, w = validate_config(cfg)
    return e, w, cfg


def validate_config(cfg: ProjectConfig) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    errors += [f"missing input: {m}" for m in cfg.missing_paths()]
    if importlib.util.find_spec("xmipy") is None:
        errors.append("python package 'xmipy' is not installed (pip install \"swatplus-modflow6-coupling[bmi]\")")
    if errors:                                   # without the inputs nothing below can be read
        if any(m.startswith("missing input") for m in errors):
            return errors, warnings
    try:
        info = mf6_prepare.inspect_simulation(cfg.modflow.sim_dir, cfg.modflow.model_name)
    except EngineInputError as exc:
        return errors + [str(exc)], warnings
    errors += info.problems
    if info.length_units != "METERS":
        errors.append(f"MODFLOW LENGTH_UNITS is '{info.length_units}'; the engine needs METERS")
    if info.time_units != "DAYS":
        errors.append(f"MODFLOW TDIS TIME_UNITS is '{info.time_units}'; the engine needs DAYS")
    if not info.of_type("sto"):
        errors.append("the MODFLOW model has no STO package")
    if not info.of_type("sfr"):
        errors.append("the MODFLOW model has no SFR package (channel runoff has nowhere to go)")
    rch = [p for p in info.of_type("rch") + info.of_type("rcha")]
    if not rch:
        errors.append("the MODFLOW model has no recharge package (RCHA)")
    elif not any("readasarrays" in p.path.read_text(errors="replace").lower() for p in rch if p.path.exists()):
        errors.append("the recharge package is list-based; the engine needs RCH with READASARRAYS (RCHA)")
    dims = (info.nlay, info.nrow, info.ncol)
    txt = cfg.swatplus.txtinout
    hru_area = {}
    try:
        hru_area = swat_io.read_hru_areas(txt)
    except EngineInputError as exc:
        errors.append(str(exc))
    try:
        hc = cio.read_hru_cells(cfg.connections.hru_cells, dims)
        if hru_area:
            e, w = cio.hru_cell_diagnostics(hc, hru_area)
            errors += e
            warnings += w
    except EngineInputError as exc:
        errors.append(str(exc))
    try:
        rc = cio.read_sfr_reaches(cfg.connections.channel_reaches, dims)
        try:
            _, ids = swat_io.read_connection_topology(txt)
            unknown = sorted(set(int(c) for c in rc.channel_id) - set(ids))
            if unknown:
                errors.append(f"sfr_reaches: channel_id {unknown[:5]} not in chandeg.con")
        except EngineInputError as exc:
            errors.append(str(exc))
    except EngineInputError as exc:
        errors.append(str(exc))
    if cfg.connections.well_cells:
        try:
            cio.read_well_cells(cfg.connections.well_cells, dims)
        except EngineInputError as exc:
            errors.append(str(exc))
    if cfg.coupling.transmission_loss is not None:
        try:
            swat_io.read_channel_geometry(txt)
        except EngineInputError as exc:
            errors.append(str(exc))
    sim_start, _ = sim_window(cfg)
    if cfg.swatplus.run_swatplus:
        ts = swat_io.read_time_sim(txt)
        if ts is not None and (ts[0] > sim_start or ts[1] < cfg.period.end):
            warnings.append(f"time.sim covers {ts[0]} to {ts[1]}, the run needs {sim_start} to "
                            f"{cfg.period.end}; SWAT+ inputs are not modified by the engine")
    else:
        for stem in ("hru_wb_day", "channel_sd_day"):
            f = swat_io.find_output(txt, stem)
            if f is None:
                errors.append(f"swatplus.run_swatplus is false but {stem}.txt/.csv is missing in {txt}")
                continue
            try:
                first, last = swat_io.output_date_range(f)
                if first > sim_start or last < cfg.period.end:
                    errors.append(f"{f.name} covers {first} to {last}; the run needs {sim_start} to "
                                  f"{cfg.period.end} ({cfg.period.spinup_days} spin-up days included)")
            except EngineInputError as exc:
                errors.append(str(exc))
    out = cfg.output.directory
    try:
        if Path(out).resolve().is_relative_to(Path(cfg.modflow.sim_dir).resolve()):
            errors.append("output.directory must not be inside modflow.sim_dir")
    except OSError:
        pass
    if cfg.coupling.recharge_update != "daily" and cfg.coupling.recharge_delay_days > 0:
        warnings.append("recharge_delay_days with monthly recharge: the delay is applied to daily "
                        "percolation first, then monthly means are formed")
    return errors, warnings
