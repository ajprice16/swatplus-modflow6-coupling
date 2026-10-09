"""Daily SWAT+ -> MODFLOW 6 coupled run through libmf6 (XMI), driven by a project file.

Per simulated day the driver (1) reads SWAT+ daily percolation and channel flow
that were prepared beforehand, (2) writes them into MODFLOW 6 memory -- SFR
``RUNOFF`` (m3/day) and the recharge array ``RECHARGE`` (m/day) -- (3) solves one
one-day step, and (4) compares what MODFLOW 6 reports having received with what
was injected. Nothing is written to the MODFLOW input files between days.

Units (``.cursorrules`` rule 1): SWAT+ percolation is mm/day over the ``hru.con``
area; MODFLOW recharge is a depth rate (m/day) applied over each cell's area;
MODFLOW volumetric terms are m3/day (a one-day step, TIME_UNITS DAYS,
LENGTH_UNITS METERS -- both asserted before the run).

Checks every step (``checks.enforce_invariants`` makes a violation stop the run,
exit code 2):

* MODFLOW 6 converged (``checks.allow_nonconvergence_steps`` days tolerated);
* the model's own percent discrepancy is within
  ``checks.mass_balance_tolerance_percent``;
* the SFR runoff held in MODFLOW memory equals the SWAT+-derived volume;
* the recharge MODFLOW 6 reports (listing budget) equals the volume the engine
  intended for the cells that have an active node;
* the budget belongs to the day just stepped (temporal sync).
"""
from __future__ import annotations

import calendar
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from ..project import ProjectConfig
from ..utils.coupling_invariants import CouplingInvariantError
from ..utils.water_budget import WaterBudget, WaterBudgetTracker
from . import connections_io as cio
from . import forcing, mf6_prepare, swat_io
from .budget import BudgetRecord, ListingBudgetReader
from .errors import EngineInputError, EngineRunError

log = logging.getLogger(__name__)
MANIFEST_FORMAT = "swatplus-modflow-run/1"
ABS_FLOOR_M3 = 1.0           # differences below this many m3/day are not flagged (budget print precision)


@dataclass
class Prepared:
    cfg: ProjectConfig
    info: mf6_prepare.SimInfo
    sim_start: date
    n_days: int
    dates: List[date]
    hru_cells: cio.HruCells
    reaches: cio.SfrReaches
    wells: Optional[cio.WellCells]
    hru_ids: List[int]
    hru_area_m2: Dict[int, float]
    perc_mm: np.ndarray
    runoff: forcing.RunoffResult
    notes: List[str] = field(default_factory=list)


# ------------------------------------------------------------------ preparation

def sim_window(cfg: ProjectConfig) -> tuple[date, int]:
    sim_start = cfg.period.start - timedelta(days=cfg.period.spinup_days)
    return sim_start, (cfg.period.end - sim_start).days + 1


def check_units(info: mf6_prepare.SimInfo) -> None:
    if info.length_units != "METERS":
        raise EngineInputError(
            f"the MODFLOW model's LENGTH_UNITS is '{info.length_units}'; the engine assumes meters "
            "(SWAT+ depths are converted mm -> m, areas are m2). Set LENGTH_UNITS METERS in the DIS file")
    if info.time_units != "DAYS":
        raise EngineInputError(
            f"the MODFLOW model's TDIS TIME_UNITS is '{info.time_units}'; the engine steps one day at a "
            "time and assumes hydraulic conductivities in m/day. Set TIME_UNITS DAYS in the TDIS file")


def run_swatplus(txtinout: Path, executable: str) -> None:
    exe = shutil.which(executable) or (str(Path(executable)) if Path(executable).exists() else None)
    if exe is None:
        raise EngineRunError(f"SWAT+ executable '{executable}' not found")
    log.info("Running SWAT+ in %s", txtinout)
    res = subprocess.run([exe], cwd=str(txtinout), capture_output=True, text=True)
    if res.returncode != 0:
        raise EngineRunError(f"SWAT+ exited with code {res.returncode}: {(res.stderr or res.stdout)[-400:]}")


def prepare_inputs(cfg: ProjectConfig) -> Prepared:
    info = mf6_prepare.inspect_simulation(cfg.modflow.sim_dir, cfg.modflow.model_name)
    if info.problems:
        raise EngineInputError("; ".join(info.problems))
    check_units(info)
    dims = (info.nlay, info.nrow, info.ncol)
    sim_start, n_days = sim_window(cfg)
    dates = forcing.day_list(sim_start, n_days)
    hc = cio.read_hru_cells(cfg.connections.hru_cells, dims)
    reaches = cio.read_sfr_reaches(cfg.connections.channel_reaches, dims)
    wells = cio.read_well_cells(cfg.connections.well_cells, dims) if cfg.connections.well_cells else None
    txt = cfg.swatplus.txtinout
    hru_area = swat_io.read_hru_areas(txt)
    errs, warns = cio.hru_cell_diagnostics(hc, hru_area)
    for w in warns:
        log.warning(w)
    if errs:
        raise EngineInputError("; ".join(errs))
    if cfg.swatplus.run_swatplus:
        run_swatplus(txt, cfg.swatplus.executable)
    notes: List[str] = list(warns)

    hru_file = swat_io.find_output(txt, "hru_wb_day")
    ch_file = swat_io.find_output(txt, "channel_sd_day")
    for f, stem in ((hru_file, "hru_wb_day"), (ch_file, "channel_sd_day")):
        if f is None:
            raise EngineInputError(f"SWAT+ output {stem}.txt/.csv not found in {txt} "
                                   "(enable daily HRU water balance and channel_sd output in print.prt)")
        first, last = swat_io.output_date_range(f)
        if first > sim_start or last < cfg.period.end:
            raise EngineInputError(
                f"{f.name} covers {first} to {last}, but the run needs {sim_start} to {cfg.period.end} "
                f"(period plus {cfg.period.spinup_days} spin-up days)")
    hru_ids = sorted({int(h) for h in hc.hru_id})
    mats, got = swat_io.read_daily_matrix(
        hru_file, ["perc"], hru_ids, sim_start, cfg.period.end,
        factors={"perc": swat_io.UNIT_TABLES["perc"]}, assumed_units={"perc": "mm"})
    perc = mats["perc"]
    if got != hru_ids:
        missing = sorted(set(hru_ids) - set(got))
        raise EngineInputError(f"{hru_file.name}: HRU(s) {missing[:5]} from hru_cells not found")
    nan = np.isnan(perc)
    if nan.any():
        bad = sorted({hru_ids[j] for j in np.where(nan.any(axis=0))[0]})
        raise EngineInputError(f"{hru_file.name}: no 'perc' value for {int(nan.sum())} (day, HRU) pairs "
                               f"(HRUs {bad[:5]}...); the file must cover every day and HRU")
    neg = int((perc < 0).sum())
    if neg:
        log.warning("%d negative percolation values set to 0", neg)
        notes.append(f"{neg} negative percolation values set to 0")
        perc = np.clip(perc, 0.0, None)

    upstream_of, con_ids = swat_io.read_connection_topology(txt)
    chan_set = set(con_ids)
    rch_channels = set(int(c) for c in np.unique(reaches.channel_id))
    unknown = sorted(rch_channels - chan_set)
    if unknown:
        raise EngineInputError(f"sfr_reaches: channel_id {unknown[:5]} not in chandeg.con")
    trans = None
    ch_vals, ch_ids = swat_io.read_daily_matrix(
        ch_file, ["flo_out", "seep"], sorted(chan_set), sim_start, cfg.period.end,
        factors={"flo_out": swat_io.UNIT_TABLES["flo_out"], "seep": swat_io.UNIT_TABLES["seep"]},
        assumed_units={"flo_out": "m^3/s", "seep": "m^3"}, optional=["seep"])
    flow = ch_vals["flo_out"]
    present_cols = [j for j in range(flow.shape[1]) if not np.isnan(flow[:, j]).all()]
    partial = [ch_ids[j] for j in present_cols if np.isnan(flow[:, j]).any()]
    if partial:
        raise EngineInputError(f"{ch_file.name}: channel(s) {partial[:5]} have missing days")
    absent_needed = sorted(rch_channels - {ch_ids[j] for j in present_cols})
    if absent_needed:
        raise EngineInputError(f"{ch_file.name}: no output rows for channel(s) {absent_needed[:5]} that "
                               "have SFR reaches")
    flow = flow[:, present_cols]
    seep = ch_vals["seep"][:, present_cols] if "seep" in ch_vals else None
    if seep is None:
        log.warning("%s has no 'seep' column; SWAT+ channel seepage is not added back", ch_file.name)
        notes.append("channel_sd_day has no seep column")
    elif np.isnan(seep).any():
        raise EngineInputError(f"{ch_file.name}: 'seep' has missing values")
    present_ids = [ch_ids[j] for j in present_cols]
    tl = cfg.coupling.transmission_loss
    if tl is not None:
        geo = swat_io.read_channel_geometry(txt)
        trans = dict(losing=None, len_km={c: geo[c][0] for c in geo}, wd_m={c: geo[c][1] for c in geo},
                     k_in_per_h=tl.k_mm_per_h / 25.4, duration_h=tl.duration_h)
    return Prepared(cfg, info, sim_start, n_days, dates, hc, reaches, wells, hru_ids, hru_area, perc,
                    _RunoffInputs(flow, seep, present_ids, reaches.reaches_by_channel(), upstream_of,
                                  reaches.n, trans), notes)


@dataclass
class _RunoffInputs:
    """Inputs of ``forcing.build_runoff``; the transmission-loss channel set is known only
    after MODFLOW heads exist, so the runoff matrix is built after initialisation."""
    flow: np.ndarray
    seep: Optional[np.ndarray]
    channel_ids: List[int]
    chan_reaches: dict
    upstream_of: dict
    n_reaches: int
    trans: Optional[dict]

    def build(self, losing: Optional[set]) -> forcing.RunoffResult:
        trans = None
        if self.trans is not None:
            trans = dict(self.trans, losing=losing or set())
        return forcing.build_runoff(self.flow, self.seep, self.channel_ids, self.chan_reaches,
                                    self.upstream_of, self.n_reaches, trans=trans)


# ------------------------------------------------------------------ MODFLOW access

class Mf6:
    """Thin addressing layer over xmipy for the pointers the driver needs."""

    def __init__(self, libmf6: Path, work_dir: Path, model_name: str):
        try:
            from xmipy import XmiWrapper
        except ImportError as exc:                      # pragma: no cover - environment dependent
            raise EngineRunError("the 'xmipy' package is required to drive libmf6 "
                                 "(pip install \"swatplus-modflow6-coupling[bmi]\")") from exc
        self.model = model_name.upper()
        self.mf = XmiWrapper(str(libmf6), working_directory=str(work_dir))
        self.mf.initialize()
        self.names = set(self.mf.get_output_var_names())

    def ptr(self, var: str, pkg: Optional[str] = None) -> np.ndarray:
        addr = self.mf.get_var_address(var.upper(), self.model, pkg.upper()) if pkg \
            else self.mf.get_var_address(var.upper(), self.model)
        return self.mf.get_value_ptr(addr)

    def packages_with(self, var: str) -> List[str]:
        v, m = var.upper(), self.model + "/"
        return sorted(n[len(m):-len(v) - 1] for n in self.names
                      if n.startswith(m) and n.endswith("/" + v) and n.count("/") == 2)

    def one_package(self, var: str, what: str) -> str:
        pk = self.packages_with(var)
        if not pk:
            raise EngineRunError(f"the MODFLOW model has no {what} package (no '{var}' variable)")
        if len(pk) > 1:
            raise EngineRunError(f"the model has several {what} packages {pk}; the engine drives one")
        return pk[0]


def _node_maps(m: Mf6, nlay: int, ncpl: int):
    nuser = nlay * ncpl
    nr = m.ptr("NODEREDUCED", "DIS") if "NODEREDUCED" in {n.split("/")[-1] for n in m.names
                                                         if n.startswith(m.model + "/DIS/")} else None
    if nr is None or nr.shape[0] != nuser:
        red = np.arange(1, nuser + 1, dtype=np.int64)
        user_of = np.arange(1, nuser + 1, dtype=np.int64)
    else:
        red = np.array(nr, dtype=np.int64)
        nu = m.ptr("NODEUSER", "DIS")
        user_of = np.array(nu, dtype=np.int64)
    return red.reshape(nlay, ncpl), user_of


def _water_table_nodes(red: np.ndarray, heads: np.ndarray, bot: np.ndarray) -> np.ndarray:
    """Reduced node (1-based; 0 = none) of the highest active, wet cell in each column.

    Where no cell is wet, the lowest active cell of the column receives the water.
    """
    nlay, ncpl = red.shape
    sel = np.zeros(ncpl, dtype=np.int64)
    chosen = np.zeros(ncpl, dtype=bool)
    for k in range(nlay):
        act = red[k] > 0
        h = np.where(act, heads[np.where(act, red[k] - 1, 0)], -np.inf)
        take = (~chosen) & act & (h > bot[k])
        sel[take] = red[k][take]
        chosen |= take
    for k in range(nlay):
        act = (red[k] > 0) & ~chosen
        sel[act] = red[k][act]
    return sel


# ------------------------------------------------------------------ main loop

def _budget_to_water_budget(rec: BudgetRecord, d: date) -> WaterBudget:
    rch = sum(rec.net(k) for k in rec.keys_of_kind("RCH", "RCHA"))
    et = sum(rec.net(k) for k in rec.keys_of_kind("EVT", "EVTA"))
    sfr_in = sum(rec.terms_in.get(k, 0.0) for k in rec.keys_of_kind("SFR"))
    sfr_out = sum(rec.terms_out.get(k, 0.0) for k in rec.keys_of_kind("SFR"))
    wel = sum(rec.net(k) for k in rec.keys_of_kind("WEL"))
    drn_out = sum(-rec.net(k) for k in rec.keys_of_kind("DRN"))
    handled = set(rec.keys_of_kind("RCH", "RCHA", "EVT", "EVTA", "SFR", "WEL", "DRN"))
    other = sum(rec.net(k) for k in rec.kinds if k not in handled and not rec.kinds[k].upper().startswith("STO"))
    return WaterBudget(date=datetime.combine(d, datetime.min.time()), recharge_m3=rch,
                       unsatisfied_et_m3=et, channel_recharge_m3=sfr_in, channel_discharge_m3=sfr_out,
                       pumping_m3=wel, drain_outflow_m3=drn_out, other_net_m3=other,
                       modflow_storage_change_m3=rec.storage_out() - rec.storage_in())


def run_daily(prep: Prepared, work_dir: Path, *, progress: Callable[[int, int, date], None] = None,
              ) -> dict:
    cfg = prep.cfg
    chk = cfg.checks
    tol = chk.mass_balance_tolerance_percent
    info = prep.info
    nlay, nrow, ncol = info.nlay, info.nrow, info.ncol
    ncpl = nrow * ncol
    n_days, dates = prep.n_days, prep.dates
    out_dir = cfg.output.directory
    out_dir.mkdir(parents=True, exist_ok=True)
    pre = cfg.output.prefix
    well_rows = None
    if prep.wells is not None:
        well_rows = [(int(l) + 1, int(r) + 1, int(c) + 1, float(q)) for l, r, c, q in
                     zip(prep.wells.layer, prep.wells.row, prep.wells.col, prep.wells.rate_m3d)]
    mf6_prepare.make_working_copy(info, work_dir, n_days, cfg.modflow.solver, well_rows=well_rows,
                                  well_auto_flow_reduce=cfg.coupling.well_auto_flow_reduce)
    m = Mf6(cfg.modflow.libmf6, work_dir, info.model_name)
    mf = m.mf
    finalized = False
    try:
        end_time, t0 = mf.get_end_time(), mf.get_current_time()
        n_steps = int(round(end_time - t0))
        if n_steps != n_days:
            raise CouplingInvariantError(
                f"temporal mismatch: MODFLOW will take {n_steps} steps for {n_days} simulated days (rule 2)")
        # ---- pointers
        red, user_of = _node_maps(m, nlay, ncpl)
        heads = m.ptr("X")
        delr, delc = np.array(m.ptr("DELR", "DIS")), np.array(m.ptr("DELC", "DIS"))
        cell_area = np.outer(delc, delr).ravel()
        bot3d = np.array(m.ptr("BOT3D", "DIS"), dtype=float).reshape(nlay, ncpl)
        rcha = m.one_package("RECHARGE", "recharge (RCHA, array-based)")
        rch_ptr, node_ptr = m.ptr("RECHARGE", rcha), m.ptr("NODELIST", rcha)
        if rch_ptr.shape[0] != ncpl or node_ptr.shape[0] != ncpl:
            raise CouplingInvariantError(
                f"RCHA arrays have {rch_ptr.shape[0]} cells, grid has {ncpl} columns (rule 4)")
        sfr = m.one_package("RUNOFF", "SFR")
        runoff_ptr = m.ptr("RUNOFF", sfr)
        if runoff_ptr.shape[0] != prep.reaches.n:
            raise CouplingInvariantError(
                f"SFR has {runoff_ptr.shape[0]} reaches but sfr_reaches.csv lists {prep.reaches.n} (rule 4)")
        igw = m.ptr("IGWFNODE", sfr)
        expect = red[prep.reaches.layer, prep.reaches.row * ncol + prep.reaches.col]
        if not np.array_equal(np.array(igw, dtype=np.int64), expect):
            raise CouplingInvariantError(
                "SFR reach cells in the MODFLOW model differ from sfr_reaches.csv (layer/row/col); "
                "the reach table does not belong to this model (rule 3)")
        simrunoff = m.ptr("SIMRUNOFF", sfr)
        # ---- initial heads
        if cfg.modflow.initial_heads is not None:
            import flopy
            hf = flopy.utils.HeadFile(str(cfg.modflow.initial_heads))
            h0 = np.asarray(hf.get_data(totim=hf.get_times()[-1]), dtype=float)
            if h0.shape != (nlay, nrow, ncol):
                raise EngineInputError(f"initial_heads has shape {h0.shape}, model is {(nlay, nrow, ncol)}")
            flat = h0.reshape(-1)
            ok = (red.ravel() > 0) & (np.abs(flat) < 1.0e20)
            heads[red.ravel()[ok] - 1] = flat[ok]
            log.info("Initial heads set from %s for %d active cells", cfg.modflow.initial_heads.name, int(ok.sum()))
        # ---- optional pointers
        evta = [p for p in m.packages_with("DEPTH") if (m.model + "/" + p + "/RATE") in m.names]
        wels = [p for p in m.packages_with("FLOWRED")]
        if cfg.coupling.et_extinction_depth_m is not None and not evta:
            raise EngineRunError("coupling.et_extinction_depth_m is set but the model has no EVT package")
        if cfg.coupling.evt_monthly_factors is not None and not evta:
            raise EngineRunError("coupling.evt_monthly_factors is set but the model has no EVT package")
        if cfg.coupling.pumping_scale != 1.0 and not wels:
            log.warning("coupling.pumping_scale is %g but the model has no WEL package; nothing to scale",
                        cfg.coupling.pumping_scale)
        # ---- transmission-loss channels (judgment: stream bed above the water table)
        losing = None
        if cfg.coupling.transmission_loss is not None:
            bed_bot = np.array(m.ptr("STRTOP", sfr)) - np.array(m.ptr("BTHICK", sfr))
            disc = np.array(heads)[np.array(igw, dtype=np.int64) - 1] < bed_bot
            df = pd.DataFrame({"c": prep.reaches.channel_id, "d": disc})
            frac = df.groupby("c")["d"].mean()
            losing = {int(c) for c, f in frac.items() if f > 0.5}
            log.info("Transmission loss applied to %d of %d channels whose stream bed lies above the "
                     "initial head in most reaches", len(losing), len(frac))
        runoff_res = prep.runoff.build(losing)
        runoff = runoff_res.runoff_m3d
        if runoff_res.unmapped_m3 != 0.0:
            msg = (f"SWAT+ channels without an SFR reach carry a net local flow of "
                   f"{runoff_res.unmapped_m3:,.0f} m3 over the run that is not injected")
            log.warning(msg)
            prep.notes.append(msg)
        # ---- recharge weights
        W, col_layer = forcing.build_weights(prep.hru_ids, prep.hru_cells, prep.hru_area_m2,
                                             cell_area, nrow, ncol)
        delayed = forcing.apply_recharge_delay(prep.perc_mm, cfg.coupling.recharge_delay_days)
        in_transit_m3 = float((cell_area * (W @ ((prep.perc_mm - delayed).sum(axis=0) * 1e-3))).sum())
        if cfg.coupling.recharge_delay_days > 0:
            msg = (f"{in_transit_m3:,.0f} m3 of percolation is still in transit through the vadose "
                   "delay at the end of the run and is not recharge")
            log.info(msg)
            prep.notes.append(msg)
        sched = forcing.RechargeSchedule(delayed, dates, W, cfg.coupling.recharge_update)
        wt = cfg.coupling.recharge_to_water_table
        static_nodes = None
        if not wt:
            sel = col_layer >= 0
            nodes = np.array(node_ptr, dtype=np.int64)
            nodes[sel] = red[col_layer[sel], np.where(sel)[0]]
            static_nodes = nodes
        trans_cols = None
        if runoff_res.loss_reach_m3d is not None:
            trans_cols = prep.reaches.row * ncol + prep.reaches.col
        # ---- loop
        reader = ListingBudgetReader(work_dir / f"{info.model_name}.lst")
        rows: List[dict] = [dict() for _ in range(n_days)]
        done_rows = [False] * n_days
        tracker = WaterBudgetTracker()
        evt_base = {}
        wel_base = {}
        n_fail = 0
        factors = cfg.coupling.evt_monthly_factors
        spin = cfg.period.spinup_days
        heads_file = out_dir / f"{pre}heads.csv"
        if cfg.output.save_heads:
            heads_file.write_text("date,layer,row,col,value\n")
        reach_files = {}
        if cfg.output.save_reach_flows:
            for nm in ("reach_outflow", "reach_gw_exchange"):
                reach_files[nm] = out_dir / f"{pre}{nm}.csv"
                reach_files[nm].write_text("date," + ",".join(str(i + 1) for i in range(prep.reaches.n)) + "\n")
        budget_rows = []
        prev_month = None
        step = max(1, n_days // 200)

        def consume(recs: List[BudgetRecord]) -> None:
            for rec in recs:
                i = rec.kper - 1
                if not (0 <= i < n_days) or done_rows[i]:
                    raise CouplingInvariantError(f"listing budget for stress period {rec.kper} does not "
                                                 "match a stepped day (temporal sync, rule 2)")
                row = rows[i]
                d = dates[i]
                rcv = sum(rec.net(k) for k in rec.keys_of_kind("RCH", "RCHA"))
                row.update(
                    recharge_mf6_m3=rcv, evt_mf6_m3=sum(rec.net(k) for k in rec.keys_of_kind("EVT", "EVTA")),
                    sfr_exchange_to_aquifer_m3=sum(rec.net(k) for k in rec.keys_of_kind("SFR")),
                    wel_mf6_m3=sum(rec.net(k) for k in rec.keys_of_kind("WEL")),
                    storage_in_m3=rec.storage_in(), storage_out_m3=rec.storage_out(),
                    total_in_m3=rec.total_in, total_out_m3=rec.total_out,
                    mass_balance_percent=rec.percent_discrepancy)
                done_rows[i] = True
                tracker.add_daily_budget(_budget_to_water_budget(rec, d))
                if cfg.output.save_budget and i >= spin:
                    br = {"date": d.isoformat()}
                    for k in rec.kinds:
                        br[f"{k}_in"] = rec.terms_in.get(k, 0.0)
                        br[f"{k}_out"] = rec.terms_out.get(k, 0.0)
                    br["total_in"], br["total_out"] = rec.total_in, rec.total_out
                    br["percent_discrepancy"] = rec.percent_discrepancy
                    budget_rows.append(br)
                # checks
                pct = rec.percent_discrepancy
                if not np.isfinite(pct):
                    raise CouplingInvariantError(f"{d}: MODFLOW budget percent discrepancy not readable")
                if abs(pct) > tol:
                    msg = (f"mass-balance violation on {d}: MODFLOW percent discrepancy {pct:.3f}% exceeds "
                           f"{tol}% (in {rec.total_in:,.1f}, out {rec.total_out:,.1f} m3/day). Rule 1.")
                    if chk.enforce_invariants:
                        raise CouplingInvariantError(msg)
                    log.warning(msg)
                want = row["recharge_intended_m3"]
                if abs(rcv - want) > max(ABS_FLOOR_M3, abs(want)) * tol / 100.0 and abs(rcv - want) > ABS_FLOOR_M3:
                    msg = (f"injection mismatch on {d}: engine put {want:,.2f} m3/day of recharge into "
                           f"active cells but MODFLOW reports receiving {rcv:,.2f} m3/day (rule 1). "
                           "Without Newton, recharge to a dry cell is dropped")
                    if chk.enforce_invariants:
                        raise CouplingInvariantError(msg)
                    log.warning(msg)

        while mf.get_current_time() < end_time:
            i = int(round(mf.get_current_time() - t0))
            d = dates[i]
            mf.prepare_time_step(0.0)
            # forcing is written AFTER prepare so it overrides what the period block read
            key = (d.year, d.month)
            new_month = key != prev_month
            if new_month and wt:
                nodes = _water_table_nodes(red, np.asarray(heads), bot3d)
                node_ptr[:] = nodes.astype(node_ptr.dtype)
            elif new_month and static_nodes is not None:
                node_ptr[:] = static_nodes.astype(node_ptr.dtype)
            if wt and cfg.coupling.recharge_update == "daily" and not new_month:
                node_ptr[:] = _water_table_nodes(red, np.asarray(heads), bot3d).astype(node_ptr.dtype)
            rate = sched.rates(i).copy()
            tl_m3 = 0.0
            if trans_cols is not None:
                loss = runoff_res.loss_reach_m3d[i]
                add = np.zeros(ncpl)
                np.add.at(add, trans_cols, loss)
                rate += add / cell_area
                tl_m3 = float(loss.sum())
            rch_ptr[:] = rate
            applied_cols = np.asarray(node_ptr) > 0
            intended = float((rate * cell_area)[applied_cols].sum())
            offgrid = float((rate * cell_area)[~applied_cols].sum())
            runoff_ptr[:] = runoff[i]
            inj, want_ro = float(np.sum(runoff_ptr)), float(runoff[i].sum())
            if chk.enforce_invariants:
                if not np.isfinite(inj):
                    raise CouplingInvariantError(f"non-finite SFR runoff injected on {d}")
                if abs(inj - want_ro) > max(abs(want_ro), 1.0) * tol / 100.0:
                    raise CouplingInvariantError(
                        f"mass-balance violation on {d}: SFR pointer holds {inj:,.1f} m3/day but the "
                        f"forcing specified {want_ro:,.1f} m3/day. Rule 1.")
            for p in evta:
                rp = m.ptr("RATE", p)
                if p not in evt_base:
                    evt_base[p] = np.array(rp, copy=True)
                rp[:] = evt_base[p] * (factors[d.month - 1] if factors else 1.0)
                if cfg.coupling.et_extinction_depth_m is not None:
                    m.ptr("DEPTH", p)[:] = cfg.coupling.et_extinction_depth_m
            wel_req = 0.0
            for p in wels:
                qp = m.ptr("Q", p)
                if p not in wel_base:
                    wel_base[p] = np.array(qp, copy=True)
                qp[:] = wel_base[p] * cfg.coupling.pumping_scale
                wel_req += float(qp.sum())
            prev_month = key

            mf.prepare_solve(1)
            kiter, converged = 0, False
            while kiter < cfg.modflow.solver.outer_maximum:
                if mf.solve(1):
                    converged = True
                    break
                kiter += 1
            mf.finalize_solve(1)
            applied_ro = np.array(simrunoff, copy=True)
            mf.finalize_time_step()
            if not converged:
                if chk.enforce_invariants and n_fail >= chk.allow_nonconvergence_steps:
                    raise CouplingInvariantError(
                        f"MODFLOW failed to converge on {d} after {kiter}/{cfg.modflow.solver.outer_maximum} "
                        "outer iterations; heads and fluxes for this day are unphysical")
                log.warning("MODFLOW did not converge on %s (%d outer iterations)", d, kiter)
                n_fail += 1
            pos_in, pos_app = float(np.clip(runoff[i], 0, None).sum()), float(np.clip(applied_ro, 0, None).sum())
            rows[i].update(
                date=d.isoformat(), spinup=bool(i < spin),
                perc_basin_mean_mm=float(prep.perc_mm[i].mean()),
                recharge_intended_m3=intended, recharge_outside_active_cells_m3=offgrid,
                transmission_loss_m3=tl_m3, runoff_intended_m3=want_ro, runoff_injected_m3=inj,
                runoff_applied_m3=float(applied_ro.sum()),
                runoff_capped_m3=want_ro - float(applied_ro.sum()),
                wel_requested_m3=wel_req, outer_iterations=kiter, converged=bool(converged))
            if chk.enforce_invariants and abs(pos_in - pos_app) > max(pos_in, ABS_FLOOR_M3) * tol / 100.0:
                raise CouplingInvariantError(
                    f"on {d} MODFLOW applied {pos_app:,.1f} m3/day of positive SFR runoff but "
                    f"{pos_in:,.1f} m3/day was injected (rule 1)")
            consume(reader.poll())
            if i >= spin:
                if cfg.output.save_heads and (d.day == calendar.monthrange(d.year, d.month)[1]
                                              or i == n_days - 1):
                    _append_heads(heads_file, d, np.asarray(heads), user_of, nlay, nrow, ncol)
                for nm, var, sgn in (("reach_outflow", "DSFLOW", 1.0), ("reach_gw_exchange", "GWFLOW", 1.0)):
                    if nm in reach_files:
                        v = sgn * np.array(m.ptr(var, sfr))
                        with open(reach_files[nm], "a") as fh:
                            fh.write(d.isoformat() + "," + ",".join(f"{x:.6g}" for x in v) + "\n")
            if progress and (i % step == 0 or i == n_days - 1):
                progress(i + 1, n_days, d)
        final_heads = np.array(heads, copy=True)
        mf.finalize()
        finalized = True
        consume(reader.poll())
        missing = [i for i, ok in enumerate(done_rows) if not ok]
        if missing:
            raise CouplingInvariantError(
                f"MODFLOW wrote no listing budget for {len(missing)} day(s), first {dates[missing[0]]}; "
                "the mass-balance check could not be completed")
    except BaseException:
        if not finalized:
            try:
                mf.finalize()
            except Exception:                                   # pragma: no cover
                pass
        _write_partial(rows if "rows" in locals() else [], out_dir, pre)
        raise
    # End-of-run check on the coupled budget, at the project's own tolerance
    # (not loosened). With enforce_invariants it fails the run like the per-step checks.
    ok_budget = tracker.validate_mass_balance(tolerance_percent=tol)
    if not ok_budget and chk.enforce_invariants:
        raise EngineRunError(
            f"coupled water budget does not close within {tol}% on at least one day "
            "(see the log); set checks.enforce_invariants: false to keep the output anyway")
    files = _write_outputs(cfg, prep, rows, budget_rows, final_heads, user_of, nlay, nrow, ncol,
                           out_dir, heads_file if cfg.output.save_heads else None, reach_files)
    return dict(files=files, n_days=n_days, n_nonconverged=n_fail, tracker_ok=bool(ok_budget),
                max_abs_percent_discrepancy=float(max(abs(r["mass_balance_percent"]) for r in rows)),
                notes=prep.notes)


def _append_heads(path: Path, d: date, heads: np.ndarray, user_of: np.ndarray,
                  nlay: int, nrow: int, ncol: int) -> None:
    full = np.full(nlay * nrow * ncol, np.nan)
    full[user_of - 1] = heads
    lay, row, col = np.unravel_index(np.arange(full.size), (nlay, nrow, ncol))
    ok = ~np.isnan(full)
    df = pd.DataFrame({"date": d.isoformat(), "layer": lay[ok] + 1, "row": row[ok] + 1,
                       "col": col[ok] + 1, "value": full[ok]})
    df.to_csv(path, mode="a", header=False, index=False)


def _write_partial(rows: List[dict], out_dir: Path, pre: str) -> None:
    done = [r for r in rows if "date" in r]
    if done:
        pd.DataFrame(done).to_csv(out_dir / f"{pre}daily_summary_partial.csv", index=False)


def _write_outputs(cfg, prep, rows, budget_rows, final_heads, user_of, nlay, nrow, ncol,
                   out_dir, heads_file, reach_files) -> List[dict]:
    pre = cfg.output.prefix
    spin = cfg.period.spinup_days
    files: List[dict] = []
    if cfg.output.save_daily_summary:
        df = pd.DataFrame(rows[spin:])
        df.to_csv(out_dir / f"{pre}daily_summary.csv", index=False)
        files.append(dict(kind="table", name="daily_summary", path=f"{pre}daily_summary.csv",
                          description="Basin daily water balance and injection ledger", units="m3/day"))
    if cfg.output.save_budget and budget_rows:
        pd.DataFrame(budget_rows).to_csv(out_dir / f"{pre}budget_terms.csv", index=False)
        files.append(dict(kind="table", name="budget_terms", path=f"{pre}budget_terms.csv",
                          description="MODFLOW 6 volumetric budget terms per day (in/out)", units="m3/day"))
    full = np.full(nlay * nrow * ncol, np.nan)
    full[user_of - 1] = final_heads
    lay, row, col = np.unravel_index(np.arange(full.size), (nlay, nrow, ncol))
    ok = ~np.isnan(full)
    pd.DataFrame({"layer": lay[ok] + 1, "row": row[ok] + 1, "col": col[ok] + 1, "value": full[ok]}).to_csv(
        out_dir / f"{pre}final_heads.csv", index=False)
    files.append(dict(kind="cell_values", name="final_heads", path=f"{pre}final_heads.csv",
                      description="Heads at the last time step", units="m"))
    if heads_file is not None:
        files.append(dict(kind="cell_values", name="heads_month_end", path=heads_file.name,
                          description="Heads at each month end and the last day", units="m"))
    for nm, p in reach_files.items():
        files.append(dict(kind="table", name=nm, path=p.name, units="m3/day",
                          description="SFR reach outflow, one column per reach number (1-based)"
                          if nm == "reach_outflow" else
                          "Stream-aquifer exchange per reach (positive = stream loses to aquifer)"))
    return files
