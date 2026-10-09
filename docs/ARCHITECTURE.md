# Architecture Overview

## System Design

The SWAT+ - MODFLOW 6 coupling framework implements a daily-timestep master-slave architecture where SWAT+ controls the main simulation loop and MODFLOW 6 is called as a subroutine for groundwater processes.

```
┌─────────────────────────────────────────────────────────────┐
│  Daily Timestep Simulation                                  │
└─────────────────────────────────────────────────────────────┘
    │
    ├─ Day Initialization
    │
    ├─► SWAT+ Simulation (advance one day)
    │   ├─ HRU soil water balance
    │   ├─ Calculate deep percolation (recharge)
    │   ├─ Calculate unsatisfied ET
    │   ├─ Route surface runoff through channels
    │   ├─ Determine irrigation demand
    │   └─ Generate channel discharge
    │
    ├─► Flux Mapping (SWAT+ → MODFLOW)
    │   ├─ Translate HRU recharge → cell recharge rates
    │   ├─ Translate unsatisfied ET → cell ET limits
    │   ├─ Translate irrigation demand → well pumping rates
    │   └─ Map channel stage to river package stage
    │
    ├─► MODFLOW 6 Simulation
    │   ├─ Update boundary conditions (RCH, EVT, WEL, RIV packages)
    │   ├─ Run groundwater flow solver
    │   ├─ Calculate heads and cell-by-cell flows
    │   └─ Extract exchange rates (channels, drains, etc)
    │
    ├─► Flux Mapping (MODFLOW → SWAT+)
    │   ├─ Aggregate cell exchange → channel baseflow
    │   ├─ Extract groundwater contribution to channels
    │   ├─ Extract drain discharge to channels
    │   └─ Update soil saturation from water table
    │
    ├─► SWAT+ Feedback Update
    │   ├─ Add baseflow to channel routing
    │   ├─ Update soil saturation
    │   ├─ Update aquifer storage
    │   └─ Complete routing calculations
    │
    ├─► State Tracking
    │   ├─ Save groundwater heads
    │   ├─ Track water balance
    │   └─ Update domain state
    │
    └─► Next Day

```

## Key Components

### 1. Simulator (`core/simulator.py`)
- Main orchestrator class `SWATPlusMODFLOWCoupler`
- Manages timestep iteration
- Coordinates model interfaces
- Handles I/O and results

### 2. Model Interfaces
- **SWATPlusInterface** (`interfaces/swatplus_interface.py`): Manages SWAT+ binary execution and I/O
- **MODFLOWInterface** (`interfaces/modflow_interface.py`): Manages MODFLOW 6 via FloPy

### 3. Coupling Logic
- **GeographicMapper** (`coupling/geographic_mapper.py`): Maps HRUs/channels to MODFLOW cells
- **FluxTranslator** (`coupling/flux_translator.py`): Converts variables between model domains

### 4. Supporting Modules
- **TimestepManager**: Handles daily iteration
- **SimulationState**: Tracks state variables
- **WaterBalanceTracker**: Maintains water balance
- **CouplingConfig**: Manages configuration

## Data Flow

### SWAT+ → MODFLOW Translation

```
SWAT+ Output            Translation              MODFLOW Input
──────────────────────  ───────────────────────  ──────────────
HRU Recharge (mm)  ──►  Weight by fraction   ──►  RCH: cell rates
                        Convert units             (m³/day)
                        Apply delay function

Unsatisfied ET (mm) ──► Weight by fraction   ──►  EVT: max rates
                        Apply extinction depth
                        Convert units

Irrigation Demand  ──►  Sum across cells     ──►  WEL: pumping
(m³/day)                               rates

Channel Stage (m)  ──►  Map 1:1 to cells    ──►  RIV: stage
```

### MODFLOW → SWAT+ Translation

```
MODFLOW Output          Translation              SWAT+ Input
──────────────────────  ───────────────────────  ──────────────
Cell Heads (m)     ──►  Check water table   ──►  Soil saturation
                        Extract saturation
                        Weight by fraction

Exchange Rates    ──►   Aggregate by channel ──► Baseflow
(m³/day)               Sum cell contributions    to channels
```

## Geographic Connectivity

Connections between SWAT+ objects and MODFLOW cells are established through GIS intersection:

- **HRU ↔ Cell**: Fractional area overlap
- **Channel ↔ Cell**: Length of channel within cell
- **Reservoir ↔ Cell**: Point or area intersection
- **Canal ↔ Cell**: Length of canal within cell
- **Drain ↔ Cell**: 1-to-1 mapping

This information is stored in a shapefile or geopackage and loaded at simulation start.

## Coupling Parameters

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

ET from saturated zone only occurs where water table is within extinction depth of surface.

### Water Balance

Daily water balance maintained at cell scale:
```
ΔStorage = Recharge + ChannelSeepage + CanalSeepage - Pumping - ET - Discharge
```

## Output Structure

```
results/
├── coupled_daily_summary.csv         # Daily watershed totals
├── coupled_cell_fluxes_2020_01.csv   # Cell-by-cell monthly flux details
├── coupled_heads_2020_01.csv         # Cell head time series
├── coupled_water_balance.txt         # Water balance report
└── coupled_state.h5                  # HDF5 with spatial state arrays
```

## Performance Considerations

- **Computational Cost**: MODFLOW typically adds 100-250% to SWAT+ runtime
- **Convergence**: Daily timesteps require stable initial conditions
- **Grid Size**: Typical MODFLOW models: 50-500 x 50-500 cells
- **Memory**: State tracking scales with #cells × #timesteps

## Limitations & Future Work

1. **Single-Layer**: Currently unconfined aquifer only
2. **Vadose Zone**: Uses transfer function, not physically-based UZF
3. **Channels**: Uniform stage across reach
4. **Nutrient Transport**: RT3D coupling development ongoing
5. **GPU**: Not yet implemented

See main README for roadmap and development status.