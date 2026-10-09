# Architecture

## Live architecture: the daily libmf6 driver

The supported way to run a coupled model is the command-line engine
(`swatplus-modflow run project.yaml`, implemented in `swatplus_modflow/engine/`).
SWAT+ runs first and writes daily output; MODFLOW 6 is then advanced **one day at a time
through libmf6 (the XMI interface)** with SWAT+ forcing written into MODFLOW memory. Nothing
is written to MODFLOW input files between days.

```
project.yaml ──► validate ──► prepare inputs ──► working copy of the MODFLOW folder ──► daily loop ──► outputs
                               │                  (TDIS = n one-day periods, IMS from        │
                               │                   the project, STO transient, OC prints     │
                               │                   the budget each step)                     │
                               ├─ hru.con areas, hru_wb_day perc  (mm/day)                   │
                               ├─ channel_sd_day flo_out, seep    (m3/s, m3)                 │
                               └─ chandeg.con / reservoir.con topology                       │
                                                                                             ▼
   for each day:  prepare_time_step → write SFR RUNOFF (m3/day) and RCHA RECHARGE (m/day) [+ EVT, WEL scaling]
                  → solve (outer iterations) → read the listing budget → check → ledger row
```

Why a daily step: monthly stress periods average a storm pulse below the stream network's
infiltration capacity, so stage dynamics on the timescale of the storm are lost.

### Modules

| module | role |
|---|---|
| `engine/swat_io.py` | SWAT+ readers; columns are found by header name, units from the units line, ids from `unit` |
| `engine/connections_io.py` | `hru_cells.csv`, `sfr_reaches.csv`, `well_cells.csv` readers and checks |
| `engine/forcing.py` | recharge delay, `daily`/`monthly`/`monthly_ramped` schedules, SFR runoff from channel flows |
| `engine/transmission.py` | optional Lane-style channel transmission loss |
| `engine/mf6_prepare.py` | inspects the MODFLOW folder and writes the daily working copy (the user's folder is never modified) |
| `engine/driver.py` | the libmf6 loop, pointers, invariants, ledger, outputs |
| `engine/budget.py` | parses MODFLOW 6's per-step volume budget from the listing file |
| `engine/validate.py`, `engine/runner.py` | `validate` and `run`, run manifest |
| `builders/` | `build-modflow` (build spec -> MODFLOW 6 model) and `build-connections` (GIS -> contract tables) |
| `cli.py`, `__main__.py` | `swatplus-modflow version | validate | run | build-modflow | build-connections` |

The file formats exchanged with a GIS front end (project file, grid, `hru_cells.csv`,
`channel_cells.csv`, `sfr_reaches.csv`, run manifest, command line and exit codes) are in
[DATA_CONTRACTS.md](DATA_CONTRACTS.md).

### Units and indexing

* SWAT+ percolation is mm/day over the **`hru.con` area**; the engine converts it to a depth rate
  (m/day) over each cell: `rate = perc_mm * 1e-3 * area_hru_con * fraction / cell_area`.
  Volumes are m3/day throughout. A polygon area that differs from `hru.con` by more than 5% is warned.
* MODFLOW 6 recharge (`RCHA`) is a depth rate applied over the cell area; cell-budget flows are m3/day,
  negative out of the cell. The engine asserts `LENGTH_UNITS METERS` and `TIME_UNITS DAYS` before the
  run (SFR uses `LENGTH_CONVERSION 1.0` and `TIME_CONVERSION 86400.0`).
* Files use 1-based `layer,row,col`; internal arrays are 0-based. Every index is range-checked.
* Reach numbers (`rno`) in `sfr_reaches.csv` start at 1, as in the SFR input file.

### Forcing

* **SFR runoff.** Each channel's local flow (`flo_out` minus the `flo_out` of the nearest upstream
  channels that have output) is spread over its reaches by length. SWAT+ `seep` (its own
  channel-to-aquifer loss) is added back, because the SFR streambed carries all channel-aquifer exchange.
  Evaporation and storage change stay as negative runoff, which MODFLOW caps at the available flow.
  A reservoir is a graph node without output, so the channel below it receives `flo_out` minus the flow of the
  channels feeding the reservoir. Channels without an SFR reach are not injected; the net volume is reported.
* **Recharge.** Optional vadose delay (below), then `daily`, `monthly` (month mean held for the month) or
  `monthly_ramped` (linear between month midpoints, per-cell rescaled so the month volume is exact).
  With `recharge_to_water_table` the highest wet active cell of each column receives it (re-evaluated at each
  month start, daily in `daily` mode); a column with no active cell is reported as `recharge_outside_active_cells_m3`.
* **Wells, ET.** `pumping_scale` multiplies every WEL package (and the optional `well_cells` table, added as
  an extra package). `et_extinction_depth_m` and the optional `evt_monthly_factors` act on the EVT package.
  No seasonal table is built in.
* **Transmission loss (optional).** The Lane loss on a channel's local flow is removed from SFR runoff and added
  to recharge in the reach cells, so water is moved, not created. Which channels lose is a judgment call: those
  whose streambed bottom lies above the initial head in most reaches.
* **Spin-up.** `period.spinup_days` days before `start` are run and checked, but not written.

### Invariants checked every step (`checks.*`)

1. MODFLOW converged (`allow_nonconvergence_steps` days tolerated).
2. The model's own **percent discrepancy** (from the listing budget) is within `mass_balance_tolerance_percent`.
3. The SFR runoff in MODFLOW memory equals the SWAT+-derived volume (m3/day); positive runoff MODFLOW applied
   equals what was injected.
4. The recharge MODFLOW reports receiving equals the volume intended for cells with an active node.
5. The budget printed belongs to the day just stepped (stress period = day index + 1).

With `enforce_invariants: true` a failure stops the run (exit code 2, manifest `status: failed`, partial ledger
`<prefix>daily_summary_partial.csv`). Every day's ledger row is in `<prefix>daily_summary.csv`.
`utils/water_budget.WaterBudgetTracker` receives each day's budget and is validated at the end of the run.

### Zone precedence in the MODFLOW builder

Hydraulic zones carry a unique integer `priority`; where zones overlap the larger one wins, and each property
(`k`, `k33_over_k`, `sy`, `ss`) is resolved separately from the zones that set it. `zone_report.csv` states, per
zone and property, cells in geometry / set / lost (and to which zone) / taken over from lower priorities. A zone
that takes over cells from others and covers more than `max_zone_share_percent` of the active cells is an error
(or a warning with `on_dominant: warn`). This replaces a fixed drawing order in which a large regional class drawn last
could cover every cell and silently make K uniform.

## Legacy facade (deprecated)

`SWATPlusMODFLOWCoupler` (`core/simulator.py`) with the FloPy `MODFLOWInterface` is the earlier file-based design.
`MODFLOWInterface.advance_one_day` changed in-memory FloPy objects and then ran `mf6` on the unchanged files on disk,
so the original model was solved every day while plausible-looking heads came back (DEFECT-38). It now raises
`FileBasedCouplingNotSupported`, and constructing the coupler emits a `DeprecationWarning`. The translation modules
(`coupling/`) and the invariant checks (`utils/coupling_invariants.py`) remain available as library code.

## Coupling parameters

### Recharge Delay (Vadose Zone Transfer Function)

Deep percolation is delayed using the SWAT+ `gw_delay` exponential transfer
function:
```
R(t) = (1 - exp(-1/δ)) * dp(t) + exp(-1/δ) * R(t-1)
```

Where:
- δ = recharge delay (days) = mean vadose residence time, typically 10-20
- dp = daily deep percolation
- R = recharge to water table

A larger δ releases *less* percolation on the day it arrives and holds more
in the vadose store: the same-day fraction is `1 - exp(-1/δ)`, which is 9.5%
at δ=10 and 4.9% at δ=20.

> **Corrected 2026-07-31.** This section previously specified
> `R(t) = δ/(δ+1)·dp(t) + 1/(δ+1)·R(t-1)`, which transposes the two weights:
> it released 90.9% of percolation on day one at δ=10 and *decreased* the lag
> as δ increased, inverting the parameter's meaning. `flux_translator.py`
> implemented that form faithfully, so the configured 10-day delay behaved as
> a ~1.1-day mean residence time. Both the equation and the code were fixed;
> `tests/test_invariant_mass_balance.py::test_recharge_delay_weights_follow_the_documented_form`
> now pins the exponential form as a regression guard.

### ET Extinction Depth

ET from the saturated zone only occurs where the water table is within the extinction depth of the surface
(the EVT package's own `DEPTH`, or `coupling.et_extinction_depth_m` when set).

## Output Structure

```
<output.directory>/
├── run_manifest.json                 # lists every file below (docs/DATA_CONTRACTS.md section 6)
├── <prefix>daily_summary.csv         # daily basin totals and the injection ledger (m3/day)
├── <prefix>budget_terms.csv          # MODFLOW budget terms per day, in/out (m3/day)
├── <prefix>final_heads.csv           # layer,row,col,value at the last step
├── <prefix>heads.csv                 # date,layer,row,col,value at each month end and the last day
├── <prefix>reach_outflow.csv, <prefix>reach_gw_exchange.csv   # optional (output.save_reach_flows)
└── mf6_work/                         # the working copy of the MODFLOW simulation (listing file, inputs)
```

## Limitations

1. Structured (DIS) grids with one GWF model; DISV/DISU and multi-model simulations are refused.
2. The model must define stress data for period 1 only; the engine carries it to every day.
3. Recharge needs an array-based package (`RCH` with `READASARRAYS`).
4. The vadose zone is a transfer function, not a physically based UZF.
5. SWAT+ is not fed back from MODFLOW heads or exchange in the daily engine; coupling is sequential
   (SWAT+ first, then MODFLOW).
6. The SWAT+ output layout was implemented from the file headers; it has not been exercised against a live SWAT+
   run in this repository's tests (the tests use synthetic files in the same layout).
