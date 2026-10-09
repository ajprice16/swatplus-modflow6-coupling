# SWAT+ – MODFLOW 6 Coupling Framework

A location-agnostic Python package that couples the SWAT+ watershed model with MODFLOW 6 for integrated
surface–subsurface simulation. Every model path, grid and coordinate system is supplied by the user; no
study-area data or defaults are included.

## What it provides

- **Daily coupled engine** (`swatplus-modflow run`): steps MODFLOW 6 one day at a time through libmf6 (XMI) with SWAT+
  percolation as recharge and SWAT+ channel flow as SFR runoff, with mass-balance and injection checks every step.
- **Builders** (`build-modflow`, `build-connections`): turn GIS layers into a MODFLOW 6 model and into the
  `hru_cells.csv` / `channel_cells.csv` tables the engine reads.
- **Project file** (`project.yaml`): the single description of a run, validated before anything starts.
- Library modules (translation, invariants, MODFLOW/SWAT+ interfaces) and a deprecated facade (see below).

## Installation

```bash
git clone <this-repo-url>
cd swatplus-modflow6-coupling
python3 -m venv venv && source venv/bin/activate
python3 -m pip install -e ".[bmi,gis]"        # engine + builders;  ".[dev]" adds the test tools
swatplus-modflow version
```

Extras: `bmi` = `xmipy`, `modflowapi` (needed by `run`); `gis` = `geopandas`, `rasterio`, `shapely`, `pyproj`
(needed by the builders); `dev` = test tools plus both. Python 3.11–3.13.

### Binaries

* **MODFLOW 6 and libmf6** (the shared library, `.so` / `.dylib` / `.dll`): `python -m flopy.utils.get_modflow <folder> --repo modflow6`
  downloads them. Point `modflow.libmf6` in the project file at the library.
* **SWAT+**: install the executable for your platform and set `swatplus.executable`, or run SWAT+ yourself and set
  `swatplus.run_swatplus: false` to use the daily outputs already in `TxtInOut`.

SWAT+ must write `hru_wb_day` and `channel_sd_day` (`.txt` or `.csv`) via `print.prt`.

## Command line

```bash
swatplus-modflow version
swatplus-modflow build-connections --grid grid.json --hrus hrus.gpkg --channels channels.gpkg --out-dir connections
swatplus-modflow build-modflow build.yaml          # writes the MODFLOW folder and modflow/sfr_reaches.csv
swatplus-modflow validate project.yaml
swatplus-modflow run project.yaml
```

`python -m swatplus_modflow <command>` is identical. Exit codes: `0` success, `1` invalid project or inputs
(nothing was run), `2` the run started and failed. `validate` prints `ERROR:` / `WARNING:` lines. `run` prints
`PROGRESS <done>/<total> <date>` lines and ends with `RESULT succeeded <absolute manifest path>` or
`RESULT failed <reason>`. Everything written is listed in `<output.directory>/run_manifest.json`. The formats are
specified in [docs/DATA_CONTRACTS.md](docs/DATA_CONTRACTS.md).

`run` does not build the MODFLOW model: build is a separate, deliberate step with its own spec. If
`connections.channel_reaches` (the `sfr_reaches.csv` that `build-modflow` writes) does not exist, `validate` and
`run` stop with an error that says to run `build-modflow` first.

## Typical workflow

1. **Grid.** Write `grid.json` (docs/DATA_CONTRACTS.md section 2): lower-left origin, uniform cells, projected CRS.
2. **Connections.** `build-connections` intersects QSWAT+ HRU polygons (id field `HRUS` by default; `--hru-id-field`)
   and channel lines (id field `Channel`; `--channel-id-field`) with the grid. Layers must have a CRS equal to the grid's
   (or pass `--reproject`). Channel direction is `as_drawn` (vertices run upstream to downstream), `reversed`, or
   inferred from a DEM. HRUs partly outside the grid keep fractions below 1; that is reported, never rescaled.
3. **MODFLOW model.** `build-modflow build.yaml` (see [build.example.yaml](build.example.yaml)) builds the model from a
   DEM, layer thicknesses or bottom rasters, a domain polygon, hydraulic zones, SFR from `channel_cells.csv`, optional EVT
   and CHD/DRN boundaries. It writes the MODFLOW 6 simulation, `sfr_reaches.csv`, `grid.json`, `grid_cells.gpkg`,
   `zone_report.csv` and `build_report.json`. Zone overlaps are resolved by an explicit, unique `priority` per zone, and
   the report says how many cells each zone set and lost; a zone that overrides others and covers too much of the domain
   fails the build (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)). Run the model once to steady state to obtain heads
   for `modflow.initial_heads`.
4. **Run.** Copy [project.example.yaml](project.example.yaml), point it at the files, `validate`, then `run`.

## Project file (summary)

`schema_version: 1`, `name`, `crs`, then sections `swatplus` (`txtinout`, `executable`, `run_swatplus`), `modflow`
(`sim_dir`, `model_name`, `libmf6`, `initial_heads`, `solver`), `connections` (`hru_cells`, `channel_reaches`; optional
`channel_cells`, `grid`, `well_cells`), `period` (`start`, `end`, `spinup_days`), `coupling` (`recharge_update`
`daily|monthly|monthly_ramped`, `recharge_delay_days`, `recharge_to_water_table`, `et_extinction_depth_m`,
`pumping_scale`, optional `evt_monthly_factors`, `well_auto_flow_reduce`, `transmission_loss`), `checks`
(`enforce_invariants`, `mass_balance_tolerance_percent`, `allow_nonconvergence_steps`) and `output` (`directory`,
`prefix`, `save_*`). Relative paths resolve against the project file's folder; unknown keys are rejected. The JSON Schema
is [docs/project_schema.json](docs/project_schema.json).

The MODFLOW model must use `LENGTH_UNITS METERS` and `TIME_UNITS DAYS`, a structured grid, an array-based recharge
package, an SFR package, and stress data for period 1 only; the engine copies the folder and drives the copy.

## Deprecated: `SWATPlusMODFLOWCoupler`

The earlier file-based facade cannot apply daily forcing (it solved the unchanged files on disk every day) and now raises
`FileBasedCouplingNotSupported` from its MODFLOW interface; constructing it emits a `DeprecationWarning`. Use the engine.
The legacy monthly scripts under `scripts/` are unchanged.

## Integrity rules

Coupling changes must preserve mass balance (m³/day), temporal aggregation, and CRS/index alignment. See `.cursorrules`
and `swatplus_modflow.utils.coupling_invariants`.

## Contributing

Generic improvements developed while applying the package to a particular watershed are welcome, provided they carry no
study-area paths, names or data. See [docs/SOFTWARE_SCOPE.md](docs/SOFTWARE_SCOPE.md); the test suite includes a guard
(`tests/test_no_study_area_content.py`) that fails on common leaks.

## Testing

```bash
pytest -q                                   # no binaries needed; end-to-end tests skip without libmf6
SWATPLUS_TEST_LIBMF6=/path/to/libmf6.so pytest -q    # runs the end-to-end tests through real libmf6
```

## License

MIT — see [LICENSE](LICENSE).

## Citation

[IN PROGRESS]

## QGIS plugin

`qgis_plugin/swatplus_modflow_qgis/` is a QGIS 3.40+/4.x plugin that prepares the grid and connection tables and runs the engine as a separate process; see [docs/QGIS_PLUGIN.md](docs/QGIS_PLUGIN.md).
