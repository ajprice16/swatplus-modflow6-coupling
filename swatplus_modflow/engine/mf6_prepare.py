"""Inspect a MODFLOW 6 simulation folder and prepare the daily working copy.

The engine never edits the user's simulation folder. It copies it into the run
folder and, in the copy only,

* replaces TDIS with ``n_days`` one-day stress periods (time units DAYS),
* replaces the IMS file with the project's solver settings,
* sets the Newton option on the model name file,
* makes the storage package transient for every period,
* replaces the output-control file so each time step prints its volume budget
  to the listing file (the engine parses it for the mass-balance check),
* optionally adds a well package from ``connections.well_cells``.

Stress-period data of the original model is kept as period 1 and carries over
to every later day. A model that defines stress data for later periods is
refused, because the engine would apply it on the wrong days.
"""
from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .errors import EngineInputError

log = logging.getLogger(__name__)

_COPY_SKIP_SUFFIXES = {".hds", ".cbc", ".lst", ".grb", ".bud", ".hed"}
_STEP_FILES = {"tdis", "ims", "oc"}
UNSUPPORTED_GRIDS = {"disv", "disu"}


@dataclass
class PackageInfo:
    ftype: str            # lower-case, without trailing '6' (e.g. 'rcha', 'sfr')
    path: Path
    pname: Optional[str]  # as written in the name file (None if absent)


@dataclass
class SimInfo:
    sim_dir: Path
    mfsim_nam: Path
    model_name: str
    model_nam: Path
    tdis_path: Path
    ims_paths: List[Path]
    packages: List[PackageInfo]
    nlay: int
    nrow: int
    ncol: int
    length_units: str
    time_units: str
    problems: List[str] = field(default_factory=list)

    def of_type(self, ftype: str) -> List[PackageInfo]:
        return [p for p in self.packages if p.ftype == ftype]


def _strip(line: str) -> str:
    for c in ("#", "!"):
        if c in line:
            line = line.split(c, 1)[0]
    return line.strip()


def _blocks(path: Path) -> Dict[str, List[List[str]]]:
    """Return ``{block name (lower): [list of non-comment lines, ...]}`` (one list per occurrence)."""
    out: Dict[str, List[List[str]]] = {}
    cur: Optional[List[str]] = None
    name = ""
    for raw in Path(path).read_text(errors="replace").splitlines():
        s = _strip(raw)
        if not s:
            continue
        low = s.lower().split()
        if low[0] == "begin":
            name = low[1]
            cur = []
            out.setdefault(name, []).append(cur)
        elif low[0] == "end":
            cur = None
        elif cur is not None:
            cur.append(s)
    return out


def _option_value(lines: List[str], key: str) -> Optional[str]:
    for ln in lines:
        t = ln.split()
        if t and t[0].lower() == key and len(t) > 1:
            return t[1]
    return None


def inspect_simulation(sim_dir: Path, model_name: Optional[str] = None) -> SimInfo:
    sim_dir = Path(sim_dir)
    nam = sim_dir / "mfsim.nam"
    if not nam.exists():
        raise EngineInputError(f"mfsim.nam not found in {sim_dir}")
    b = _blocks(nam)
    timing = [ln.split() for blk in b.get("timing", []) for ln in blk]
    tdis = next((t[1] for t in timing if t[0].lower() == "tdis6"), None)
    if tdis is None:
        raise EngineInputError("mfsim.nam: no TDIS6 entry")
    models = [ln.split() for blk in b.get("models", []) for ln in blk]
    problems: List[str] = []
    gwf = [m for m in models if m[0].lower() == "gwf6"]
    others = [m for m in models if m[0].lower() != "gwf6"]
    if others:
        raise EngineInputError("simulation contains non-GWF models (%s); the engine drives a single "
                               "GWF model" % ", ".join(m[0] for m in others))
    if not gwf:
        raise EngineInputError("mfsim.nam: no GWF6 model")
    if any(blk for blk in b.get("exchanges", [])):
        raise EngineInputError("simulation defines exchanges (multi-model); not supported")
    if model_name:
        sel = [m for m in gwf if len(m) > 2 and m[2].lower() == model_name.lower()]
        if not sel:
            raise EngineInputError(f"modflow.model_name '{model_name}' not in simulation "
                                   f"(models: {[m[2] for m in gwf if len(m) > 2]})")
        model = sel[0]
    else:
        if len(gwf) > 1:
            raise EngineInputError("simulation has several GWF models; set modflow.model_name")
        model = gwf[0]
    mname = model[2] if len(model) > 2 else Path(model[1]).stem
    model_nam = sim_dir / model[1]
    ims = []
    for blk in b.get("solutiongroup", []):
        for ln in blk:
            t = ln.split()
            if t[0].lower() == "ims6":
                ims.append(sim_dir / t[1])
    if not ims:
        raise EngineInputError("mfsim.nam: no IMS6 entry")
    mb = _blocks(model_nam)
    pk_lines = [ln.split() for blk in mb.get("packages", []) for ln in blk]
    packages = [PackageInfo(t[0].lower().rstrip("6") if t[0].lower().endswith("6") else t[0].lower(),
                            sim_dir / t[1], t[2] if len(t) > 2 else None) for t in pk_lines]
    dis = [p for p in packages if p.ftype in ("dis",) or p.ftype in UNSUPPORTED_GRIDS]
    if not dis:
        raise EngineInputError(f"{model_nam.name}: no discretization package")
    if dis[0].ftype in UNSUPPORTED_GRIDS:
        raise EngineInputError(f"discretization '{dis[0].ftype.upper()}6' is not supported; the engine "
                               "needs a structured (DIS) grid, as in docs/DATA_CONTRACTS.md")
    db = _blocks(dis[0].path)
    dims = db.get("dimensions", [[]])[0]
    try:
        nlay, nrow, ncol = (int(_option_value(dims, k)) for k in ("nlay", "nrow", "ncol"))
    except (TypeError, ValueError):
        raise EngineInputError(f"{dis[0].path.name}: cannot read NLAY/NROW/NCOL") from None
    length_units = (_option_value(db.get("options", [[]])[0], "length_units") or "UNDEFINED").upper()
    tb = _blocks(sim_dir / tdis)
    time_units = (_option_value(tb.get("options", [[]])[0], "time_units") or "UNDEFINED").upper()
    # stress data after period 1 would be applied on the wrong simulated days
    for p in packages:
        if p.ftype in ("dis", "ic", "npf", "sto", "oc") or not p.path.exists():
            continue
        for m in re.finditer(r"^\s*begin\s+period\s+(\d+)", p.path.read_text(errors="replace"),
                             re.IGNORECASE | re.MULTILINE):
            if int(m.group(1)) > 1:
                problems.append(f"{p.path.name}: defines stress data for period {m.group(1)}; the daily "
                                "engine uses period 1 only. Provide a single-period model")
                break
    return SimInfo(sim_dir, nam, mname, model_nam, sim_dir / tdis, ims, packages, nlay, nrow, ncol,
                   length_units, time_units, problems)


def _tdis_text(n_days: int) -> str:
    return ("BEGIN options\n  TIME_UNITS DAYS\nEND options\n\n"
            f"BEGIN dimensions\n  NPER {n_days}\nEND dimensions\n\n"
            "BEGIN perioddata\n" + "".join("  1.0 1 1.0\n" for _ in range(n_days)) + "END perioddata\n")


def _ims_text(solver, *, newton_ur: bool) -> str:
    lines = ["BEGIN options", "  PRINT_OPTION SUMMARY", f"  COMPLEXITY {solver.complexity.upper()}",
             "END options", "", "BEGIN nonlinear",
             f"  OUTER_DVCLOSE {solver.outer_dvclose:.10g}",
             f"  OUTER_MAXIMUM {solver.outer_maximum}",
             f"  UNDER_RELAXATION {'DBD' if solver.under_relaxation else 'NONE'}",
             "END nonlinear", "", "BEGIN linear",
             f"  INNER_MAXIMUM {solver.inner_maximum}",
             f"  INNER_DVCLOSE {solver.inner_dvclose:.10g}"]
    if getattr(solver, "inner_rclose", None) is not None:
        lines.append(f"  INNER_RCLOSE {solver.inner_rclose:.10g}")
    lines += ["END linear", ""]
    return "\n".join(lines)


_OC_TEXT = ("BEGIN options\nEND options\n\nBEGIN period 1\n  PRINT BUDGET ALL\nEND period\n")


def _set_newton(nam_text: str, newton: bool, under_relaxation: bool) -> str:
    out, in_opt, wrote = [], False, False
    want = None if not newton else ("NEWTON UNDER_RELAXATION" if under_relaxation else "NEWTON")
    for raw in nam_text.splitlines():
        s = _strip(raw).lower()
        if s.startswith("begin options"):
            in_opt = True
            out.append(raw)
            continue
        if in_opt and s.startswith("end options"):
            if want and not wrote:
                out.append("  " + want)
            in_opt = False
            out.append(raw)
            continue
        if in_opt and s.split()[:1] == ["newton"]:
            if want and not wrote:
                out.append("  " + want)
                wrote = True
            continue
        out.append(raw)
    text = "\n".join(out) + "\n"
    if want and "begin options" not in nam_text.lower():
        text = "BEGIN options\n  " + want + "\nEND options\n\n" + text
    return text


def _sto_transient(sto_text: str) -> str:
    kept, skipping = [], False
    for raw in sto_text.splitlines():
        s = _strip(raw).lower()
        if s.startswith("begin period"):
            skipping = True
            continue
        if skipping:
            if s.startswith("end period"):
                skipping = False
            continue
        kept.append(raw)
    return "\n".join(kept).rstrip() + "\n\nBEGIN period 1\n  TRANSIENT\nEND period\n"


def make_working_copy(info: SimInfo, work_dir: Path, n_days: int, solver, *,
                      well_rows: Optional[List[Tuple[int, int, int, float]]] = None,
                      well_auto_flow_reduce: Optional[float] = None) -> Path:
    """Copy ``info.sim_dir`` to ``work_dir`` and rewrite the daily-stepping files there.

    ``well_rows`` are 1-based ``(layer, row, col, rate)``. Returns the working
    simulation directory.
    """
    work_dir = Path(work_dir)
    if work_dir.exists() and any(work_dir.iterdir()):
        raise EngineInputError(f"working folder {work_dir} is not empty; refusing to overwrite")
    work_dir.parent.mkdir(parents=True, exist_ok=True)
    src = info.sim_dir.resolve()
    if work_dir.resolve().is_relative_to(src):
        raise EngineInputError("the output directory must not be inside modflow.sim_dir "
                               "(it would be copied into itself)")

    def ignore(d, names):
        return [n for n in names if Path(n).suffix.lower() in _COPY_SKIP_SUFFIXES]

    shutil.copytree(src, work_dir, ignore=ignore)
    rel = lambda p: work_dir / Path(p).resolve().relative_to(src)
    (rel(info.tdis_path)).write_text(_tdis_text(n_days))
    for ims in info.ims_paths:
        rel(ims).write_text(_ims_text(solver, newton_ur=solver.newton and solver.under_relaxation))
    mnam = rel(info.model_nam)
    text = _set_newton(mnam.read_text(), solver.newton, solver.under_relaxation)
    sto = info.of_type("sto")
    if not sto:
        raise EngineInputError("the model has no STO package; a daily transient run needs storage")
    p = rel(sto[0].path)
    p.write_text(_sto_transient(p.read_text()))
    oc = info.of_type("oc")
    if oc:
        rel(oc[0].path).write_text(_OC_TEXT)
    else:
        (work_dir / "swatplus_engine.oc").write_text(_OC_TEXT)
        text = re.sub(r"(?im)^(\s*end\s+packages)", "  OC6  swatplus_engine.oc  swatplus_oc\n\\1", text, count=1)
    if well_rows:
        opts = "BEGIN options\n  SAVE_FLOWS\n"
        if well_auto_flow_reduce is not None:
            opts += f"  AUTO_FLOW_REDUCE {well_auto_flow_reduce:.10g}\n"
        body = "".join(f"  {l} {r} {c} {q:.10g}\n" for l, r, c, q in well_rows)
        (work_dir / "swatplus_wells.wel").write_text(
            opts + f"END options\n\nBEGIN dimensions\n  MAXBOUND {len(well_rows)}\nEND dimensions\n\n"
            f"BEGIN period 1\n{body}END period\n")
        text = re.sub(r"(?im)^(\s*end\s+packages)", "  WEL6  swatplus_wells.wel  swatplus_wel\n\\1", text, count=1)
    mnam.write_text(text)
    return work_dir
