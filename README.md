# SWAT+ and MODFLOW 6 Coupling Framework

A Python-based framework for bidirectional coupling of the SWAT+ watershed model with MODFLOW 6 groundwater flow model for integrated surface-subsurface hydrologic simulation.

## Overview

This project implements the coupling methodology described in [Bailey et al. (2025)](https://gmd.copernicus.org/articles/18/5681/2025/) adapted for modern MODFLOW 6 and SWAT+ versions. The framework enables:

- **Daily timestep integration** of SWAT+ surface/near-surface hydrology with MODFLOW 6 groundwater dynamics
- **Bidirectional data exchange** including:
  - Soil recharge to groundwater
  - Groundwater discharge to surface features (channels, canals)
  - Groundwater-surface water interactions
  - Irrigation demand-driven pumping
  - ET from saturated zone
  - Tile drain discharge
  - Reservoir-aquifer interactions

## Architecture

### Core Components

```
swatplus_modflow/
├── core/
│   ├── simulator.py          # Main coupling simulator
│   ├── timestep_manager.py   # Daily time stepping logic
│   ├── state_manager.py      # Simulation state tracking
│   └── water_balance.py      # Water balance calculations
├── interfaces/
│   ├── swatplus_interface.py # SWAT+ I/O management
│   ├── modflow_interface.py  # MODFLOW 6 I/O via FloPy
│   └── binary_runner.py      # Executable management
├── coupling/
│   ├── geographic_mapper.py  # HRU/Channel ↔ MODFLOW cell mapping
│   ├── flux_translator.py    # Variable translation between models
│   └── boundary_conditions.py # Dynamic GIS connectivity
└── utils/
    ├── io_utils.py           # File I/O helpers
    ├── gis_utils.py          # Spatial operations
    └── config.py             # Configuration management
```

## Key Coupling Fluxes

| Flux | Direction | SWAT+ Object | MODFLOW Package | Description |
|------|-----------|--------------|-----------------|-------------|
| Recharge | Soil → Aquifer | HRU soil | RCH | Deep percolation reaching water table |
| Soil Transfer | Aquifer → Soil | HRU soil | STO | Groundwater rising into soil profile |
| Groundwater ET | Aquifer → Atm | HRU | EVT | ET from saturated zone |
| Channel Exchange | Aquifer ↔ Channel | Stream reach | RIV | Darcy flow based on head gradient |
| Irrigation Pumping | Aquifer → HRU | Irrigated HRU | WEL | Demand-driven well extraction |
| Canal Seepage | Aquifer ↔ Canal | Canal network | RIV | Seepage through canal bed |
| Tile Drainage | Aquifer → Channel | Subsurface drain | DRN | Drain package outflow to channels |
| Reservoir Exchange | Aquifer ↔ Reservoir | Reservoir | RES | Exchange through reservoir bed |

## Data Flow (Daily Timestep)

```
SWAT+ Simulation Day
    ↓
[Calculate HRU processes]
    ↓
[Calculate soil percolation → recharge]
[Calculate unsatisfied ET demand]
[Update channel stages]
[Determine irrigation demand → pumping requirements]
    ↓
[Map SWAT+ variables → MODFLOW cells]
    ↓
MODFLOW 6 Executed
    ↓
[Solve groundwater head and flow]
[Calculate cell-by-cell exchanges]
[Output MODFLOW results]
    ↓
[Map MODFLOW results → SWAT+ objects]
    ↓
[Update channel baseflow]
[Update soil saturation]
[Complete SWAT+ routing]
    ↓
Next Day
```

## Installation

### Prerequisites

- Python 3.9+
- MODFLOW 6 executable (compiled binary)
- SWAT+ executable (compiled binary)
- Git

### Setup

```bash
# Clone repository
git clone https://github.com/ajprice16/swatplus-modflow6-coupling.git
cd swatplus-modflow6-coupling

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install package in development mode
python3 -m pip install -e ".[dev]"

# Verify installation
python3 -c "import swatplus_modflow; print(swatplus_modflow.__version__)"
```

Avoid `python setup.py install`; use the editable install above so the package is installed into the active virtual environment.

### Configuring Binaries

Create a `config.yaml` in your project root:

```yaml
modflow:
  executable: "/path/to/mf6"  # MODFLOW 6 binary
  version: "6.4"

swatplus:
  executable: "/path/to/swatplus"  # SWAT+ binary
  version: "61.0"

simulation:
  start_date: "2020-01-01"
  end_date: "2025-12-31"
  timestep_days: 1
```

## Quick Start

```python
from swatplus_modflow import SWATPlusMODFLOWCoupler

# Initialize coupler
coupler = SWATPlusMODFLOWCoupler(
    modflow_dir="path/to/modflow/model",
    swatplus_dir="path/to/swatplus/model",
    geo_connections_file="connections.shp",  # GIS file with HRU/Channel ↔ MODFLOW cell mapping
    config_file="config.yaml"
)

# Load geographic connections
coupler.load_geographic_connections()

# Run coupled simulation
coupler.run(
    start_date="2020-01-01",
    end_date="2020-12-31",
    output_dir="./results"
)

# Access results
df_flows = coupler.get_daily_flows()
df_heads = coupler.get_daily_heads()
```

## Preparing Geographic Connections

The coupling requires a **GIS connections file** (shapefile or geopackage) that maps spatial relationships between SWAT+ objects and MODFLOW grid cells:

- SWAT+ HRUs → MODFLOW cells (with area fractions)
- SWAT+ stream channels → MODFLOW cells (with segment lengths)
- SWAT+ reservoirs → MODFLOW cells (optional)

### Quick Start

**5-Minute Guide:** [docs/GIS_QUICKSTART.md](docs/GIS_QUICKSTART.md)

**Detailed Guide:** [docs/GIS_PREPROCESSING.md](docs/GIS_PREPROCESSING.md)

### Required Attributes

| Attribute      | Type    | Description                          |
|----------------|---------|--------------------------------------|
| swat_obj_type  | String  | "hru", "channel", or "reservoir"    |
| swat_obj_id    | Integer | SWAT+ object ID                     |
| mf_layer       | Integer | MODFLOW layer (1-based)             |
| mf_row         | Integer | MODFLOW row (1-based)               |
| mf_col         | Integer | MODFLOW column (1-based)            |
| fraction       | Float   | Spatial overlap fraction (for HRUs) |
| length_m       | Float   | Segment length (for channels)       |

### QGIS Workflow Summary

1. Load HRU polygons and MODFLOW grid into QGIS
2. Intersect layers (`Vector → Geoprocessing → Intersection`)
3. Calculate area fractions (HRUs) or segment lengths (channels)
4. Populate MODFLOW row/col indices from grid
5. Export as GeoPackage

**Or use automated script:** [examples/qgis_create_connections.py](examples/qgis_create_connections.py)

### Validating Connections File

After creating your connections file, validate it:

```bash
python examples/validate_connections.py connections.gpkg --modflow-grid 50 100 3
```

This checks for:
- ✓ Required attributes present
- ✓ HRU fraction sums ≈ 1.0
- ✓ MODFLOW indices within grid bounds
- ✓ No duplicate connections
- ✓ Channel lengths > 0

## Configuration

### Model Parameters

Key configurable parameters in each SWAT+ and MODFLOW model:

**SWAT+:**
- Recharge delay parameter (vadose zone transfer function)
- ET extinction depth for groundwater ET
- Water allocation rules for irrigation demand

**MODFLOW 6:**
- Recharge package (RCH) - receives daily recharge from SWAT+
- River package (RIV) - simulates channel/canal exchange
- Evapotranspiration package (EVT) - receives unsatisfied ET from SWAT+
- Well package (WEL) - simulates irrigation pumping
- Drain package (DRN) - simulates tile drain discharge

## Key Concepts

### Water Balance
Daily water balance is maintained across both models:
```
ΔStorage_aquifer = Recharge + ChannelSeepage + CanalSeepage - Pumping - ET - ChannelDischarge
```

### Transfer Functions
Deep percolation is temporally distributed to water table using transfer function:
```
R(i) = δ/(δ+1) * dpi + 1/(δ+1) * R(i-1)
```
where δ = recharge delay (days typically 5-20)

### State Variables Maintained
- MODFLOW groundwater head (m)
- SWAT+ soil water content (mm)
- Daily streamflow contributions from baseflow
- Irrigation demand and satisfaction via pumping
- Canal/channel stages and exchange rates

## Output Files

### SWAT+ Outputs (existing)
- `output.std` - Standard output
- SWAT+ report files
- Channel flow series

### Coupled Model Outputs
- `smrt_daily_flows.csv` - Daily groundwater fluxes (mm, watershed-normalized)
- `smrt_cell_flows_YYYY_MM.csv` - Cell-by-cell fluxes (m³/day)
- `smrt_daily_heads.csv` - Daily average groundwater heads (m)
- `smrt_water_balance.csv` - Daily water balance check
- `smrt_state.h5` - HDF5 with spatial state arrays

## Literature

- **Bailey et al. (2025)**: SWAT+MODFLOW model. *Geoscientific Model Development*, 18, 5681–5697. https://doi.org/10.5194/gmd-18-5681-2025

- **Original SWAT-MODFLOW:**
  - Bailey et al. (2016): SWAT-MODFLOW Coupling. *Hydrological Processes*
  - Kim et al. (2008): SWAT-MODFLOW Framework. *Journal of Hydrology*

- **MODFLOW 6:**
  - Langevin et al. (2017): MODFLOW 6 Documentation. *USGS Techniques and Methods*

- **SWAT+:**
  - Bieger et al. (2017): Introduction to SWAT+. *JAWRA*

## Development

### Testing
```bash
pytest tests/ -v --cov=swatplus_modflow
```

### Code Style
```bash
black swatplus_modflow tests
flake8 swatplus_modflow tests
mypy swatplus_modflow
```

### Adding New Features
1. Create a feature branch: `git checkout -b feature/new-feature`
2. Add tests in `tests/`
3. Ensure all tests pass: `pytest`
4. Submit pull request

## Known Limitations

1. **Vadose Zone**: Currently uses transfer function; UZF package not yet implemented
2. **Aquifer Layers**: Single-layer unconfined aquifers only (multi-layer coming)
3. **Channel Representation**: Uniform channel depth across reach; NHD+ segments recommended
4. **Confined Aquifers**: Not currently supported
5. **Nutrient Transport**: RT3D coupling under development

## Roadmap

- [ ] Multi-layer aquifer support
- [ ] UZF package for vadose zone processes
- [ ] RT3D coupling for nutrient transport
- [ ] Uncertainty quantification framework
- [ ] Graphical user interface (QGIS plugin)
- [ ] GPU acceleration for large grids
- [ ] Cloud deployment scripts

## Contributing

Contributions welcome! Please:
1. Open an issue for discussion
2. Fork repository
3. Follow code style guidelines (black, flake8)
4. Add tests for new features
5. Submit pull request with clear description

## License

MIT License - See [LICENSE](LICENSE) file for details

## Citation

[IN PROGRESS]

## Contact

For questions or issues:
- Open a GitHub issue
- Create a discussion
- Email: [ajprice@mail.wlu.edu]

## Acknowledgments

- Original SWAT+MODFLOW code authors (Bailey et al.)
- SWAT+ development team (Texas A&M)
- MODFLOW 6 development team (USGS)
