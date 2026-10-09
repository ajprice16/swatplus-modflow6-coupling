# SWAT+ – MODFLOW 6 Coupling Framework

A location-agnostic Python framework for coupling the SWAT+ watershed model
with MODFLOW 6 for integrated surface–subsurface hydrologic simulation.

The package is location-agnostic: every model path, grid and coordinate
system is supplied by the user. No study-area data or defaults are included.

## What this package provides

- **Library** (`swatplus_modflow/`): geographic mapping, flux translation,
  FloPy and BMI/XMI MODFLOW interfaces, coupling invariants (mass balance,
  CRS, temporal sync, indexing), and a high-level `SWATPlusMODFLOWCoupler`
  facade.
- **CLIs / scripts**: path-parameterized monthly coupler and SWAT+
  calibration drivers, environment checks, STR→RIV conversion, and a
  smalltest MF6 builder that requires explicit watershed paths.
- **Examples & GIS docs**: connection-file workflow for any watershed.
- **Tests**: unit and invariant tests with no study-area hardcodes.

## Architecture note

The public `SWATPlusMODFLOWCoupler` API is the stable library surface
documented below. Production daily in-memory coupling (libmf6 / XMI) is
evolving into this package; application-specific runners stay outside
this repository. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
and [docs/SOFTWARE_SCOPE.md](docs/SOFTWARE_SCOPE.md).

## Installation

```bash
git clone <this-repo-url>
cd swatplus-modflow6-coupling
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -e ".[dev]"
python3 -c "import swatplus_modflow; print(swatplus_modflow.__version__)"
```

### Binaries

Install MODFLOW 6 and SWAT+ for your platform, then point at them in
`config.yaml` (see [config.example.yaml](config.example.yaml)) or via CLI flags
/ environment variables used by the generic scripts.

## Quick start (library)

```python
from swatplus_modflow import SWATPlusMODFLOWCoupler

coupler = SWATPlusMODFLOWCoupler(
    modflow_dir="path/to/modflow/model",
    swatplus_dir="path/to/swatplus/TxtInOut",
    geo_connections_file="connections.gpkg",
    config_file="config.yaml",
)
coupler.load_geographic_connections()
coupler.run(start_date="2020-01-01", end_date="2020-12-31", output_dir="./results")
```

## Generic CLIs

```bash
# Monthly one-way / two-way coupler (all paths required)
python scripts/run_coupled_model_generic.py \
  --swat-dir /path/to/TxtInOut \
  --mf6-exe /path/to/mf6 \
  --mf6-model /path/to/model \
  --start-year 1995 --end-year 2019

# Toy MF6 grid over any QSWAT+ watershed
python scripts/create_smalltest_modflow_model.py \
  --domain /path/to/lsus2.shp \
  --swat-txt /path/to/TxtInOut \
  --mf6 /path/to/mf6
```

## Geographic connections

See [docs/GIS_QUICKSTART.md](docs/GIS_QUICKSTART.md) and
[docs/GIS_PREPROCESSING.md](docs/GIS_PREPROCESSING.md). Validate with:

```bash
python examples/validate_connections.py connections.gpkg --modflow-grid NROW NCOL NLAY
```

## Integrity rules

Coupling changes must preserve mass balance (m³/day), temporal aggregation,
and CRS/index alignment. See `.cursorrules` and
`swatplus_modflow.utils.coupling_invariants`.

## Contributing

Generic improvements developed while applying the package to a particular
watershed are welcome, provided they carry no study-area paths, names or data.
See [docs/SOFTWARE_SCOPE.md](docs/SOFTWARE_SCOPE.md); the test suite includes
a guard (`tests/test_no_study_area_content.py`) that fails on common leaks.

## Testing

```bash
pytest tests/ -v
```

## License

MIT — see [LICENSE](LICENSE).

## Citation

[IN PROGRESS]

## QGIS plugin

`qgis_plugin/swatplus_modflow_qgis/` is a QGIS 3.40+/4.x plugin that prepares the grid and connection tables and runs the engine as a separate process; see [docs/QGIS_PLUGIN.md](docs/QGIS_PLUGIN.md).
