# QGIS plugin: SWAT+ MODFLOW 6 Coupling

The plugin prepares the engine's inputs with native QGIS tools and runs the engine as a separate
process. Files exchanged with the engine are defined in [DATA_CONTRACTS.md](DATA_CONTRACTS.md); the plugin
never imports the engine.

* QGIS 3.40 or later (Qt5) and QGIS 4.x (Qt6); `metadata.txt` declares `qgisMinimumVersion=3.40`,
  `qgisMaximumVersion=4.99`, `supportsQt6=True`.
* Uses only what QGIS ships: `qgis.core`, `qgis.gui`, `qgis.processing`, `qgis.PyQt` and the standard library.
  No flopy, geopandas, pydantic, PyYAML or numpy is imported by the plugin.

## Install

The plugin is distributed through GitHub only (it is not in the official QGIS plugin repository). Download
`swatplus_modflow_qgis-<version>.zip` from the
[releases page](https://github.com/ajprice16/swatplus-modflow6-coupling/releases) and install it with
*Plugins > Manage and Install Plugins > Install from ZIP*. To build the same archive from a checkout
(standard library only):

```
python qgis_plugin/build_plugin_zip.py          # writes qgis_plugin/build/swatplus_modflow_qgis-<version>.zip
```

The archive's only top-level folder is `swatplus_modflow_qgis/`; tests and caches are excluded. The plugin is
flagged experimental.
For development, link or copy `qgis_plugin/swatplus_modflow_qgis/` into the `python/plugins` folder of a QGIS
profile.

## Workflow

All inputs must share one projected CRS in metres (nothing is reprojected silently; use the native
*Reproject layer* tool first). Find the tools in the Processing Toolbox under **SWAT+ MODFLOW 6**, or from the
panel opened by the toolbar button.

1. **Create MODFLOW grid**: domain polygon layer, cell size (no default), optional buffer, optional snapping of
   the origin to a multiple of the cell size. Outputs a GeoPackage with integer fields `row`, `col`, `active`
   (1 when the cell overlaps the domain; touching the boundary only does not count) and `grid.json`.
   The grid is uniform and unrotated; row 1 is the northernmost row, column 1 the westernmost, indices are 1-based.
2. **Build HRU connections**: HRU polygons, the id field (default `HRUS`, the HRU id of the QSWAT+ `hrus2` layer),
   the grid layer and the MODFLOW layer that receives recharge. Outputs `hru_cells.csv`
   (`hru_id, layer, row, col, area_m2, fraction`). `fraction` is relative to the full polygon area of the HRU
   (polygons sharing an id are combined), so an HRU inside the grid sums to 1. HRUs that are partly or wholly
   outside the grid are listed in the log and are not rescaled.
3. **Build channel connections**: channel lines, the id field (default `Channel`, as in the QSWAT+ `rivs1` layer),
   the grid layer, the MODFLOW layer. Outputs `channel_cells.csv`
   (`channel_id, seq, layer, row, col, length_m, fraction`).
   * Assumption: each channel is digitised from upstream to downstream. `seq` counts pieces from the upstream end.
   * Tick **Reverse** if your layer runs the other way. Or supply a **DEM**: the elevation at both ends of each
     channel decides its direction (downhill is downstream) and overrides both the digitising direction and
     *Reverse*; channels the DEM cannot decide (equal elevations, no data) fall back to the digitising direction.
     The log counts how many channels the DEM reversed.
   * Multipart features are chained end to end (parts are flipped as needed); joins wider than the tolerance are
     reported. A channel that leaves a cell and re-enters it gets a second piece with its own `seq`.
   * `fraction` is relative to the whole channel length, including any part outside the grid.
4. **Validate inputs**: projected CRS, matching CRSs, the grid layer against `grid.json`, grid coverage of the
   domain and consistency of `active`, ids present and integer, and, for the tables, fraction sums, `seq` runs,
   cells inside the grid and areas/lengths against the layers. Writes a readable report (log and optional HTML);
   partial coverage is a warning because that water leaves the model.
5. **Project and run engine** (panel button): fill in the form or load a project file written by the plugin, write
   it, then *Validate with engine* or *Run*. The project file is written as JSON, which the engine's YAML loader
   accepts; relative paths are kept relative to the project file when the targets lie inside its folder. An
   existing hand-written YAML project can be run unchanged by unticking *Use the form* (the plugin does not parse
   YAML). The run streams the engine log, drives the progress bar from `PROGRESS <done>/<total> <date>` lines,
   can be cancelled, and on `RESULT succeeded <manifest>` loads `run_manifest.json` into a layer group: `table`
   files as tables and `cell_values` files joined to the grid layer chosen in the dialog on `row`/`col` (one
   polygon layer per MODFLOW layer; if the file has a `date` column only the latest date is shown, the complete
   CSV remains in the results folder). Without a grid layer, cell values are loaded as tables.

## Engine setup

The engine needs its own Python environment (flopy, a MODFLOW 6 library, ...). In *Engine settings* give the
path of that environment's interpreter; the plugin runs

```
<python> -m swatplus_modflow version
<python> -m swatplus_modflow validate <project>
<python> -m swatplus_modflow run <project>
```

*Check engine* runs `version` and, when a project file is given, `validate`. Exit codes follow the contract
(0 success, 1 invalid inputs, 2 failed run). The optional helper creates a virtual environment and installs
the package from a local folder or a URL; the exact commands are shown and must be confirmed before they run:

```
<base python> -m venv <folder>
<folder>/bin/python -m pip install --upgrade pip
<folder>/bin/python -m pip install <package folder, archive or URL>
```

(`Scripts\python.exe` on Windows.) The interpreter path is stored in the QGIS settings of the current profile.

## Tests

* `PYTHONPATH=. python -m pytest qgis_plugin/tests -q`: standard-library modules, the project file against the
  engine's `load_project`, packaging, the fake engine against the CLI contract, and a static scan for Qt5/Qt6
  incompatibilities (`exec_`, unscoped enums, `QVariant` field types, direct PyQt imports).
* Headless tests inside QGIS's own Python (algorithms on synthetic layers, plugin load/unload, installing the zip
  into a temporary profile, run dialog with a fake engine that obeys the CLI contract). They use a temporary
  profile, never yours:

  | QGIS install | command |
  |---|---|
  | macOS app bundle | `QGIS_APP=/Applications/QGIS.app qgis_plugin/tests/run_qgis_tests.sh` |
  | Linux packages / qgis Docker images | `qgis_plugin/tests/run_qgis_tests.sh` |
  | Linux Flatpak | `QGIS_FLATPAK=1 qgis_plugin/tests/run_qgis_tests.sh` |
  | Windows standalone installer | `qgis_plugin\tests\run_qgis_tests.bat "C:\Program Files\QGIS 3.44.15"` |
  | Windows OSGeo4W (every QGIS build in it) | `qgis_plugin\tests\run_qgis_tests.bat C:\OSGeo4W` |
  | Windows OSGeo4W (one build) | `qgis_plugin\tests\run_qgis_tests.bat C:\OSGeo4W --variant ltr` |

  An OSGeo4W root can hold several QGIS builds, each with its own launcher in `bin`:
  `python-qgis-ltr.bat` (variant `ltr`), `python-qgis.bat` (`qgis`), `python-qgis-qt6.bat` (`qt6`) and
  `python-qgis-dev.bat` (`dev`). The runner uses every launcher it finds, each in a separate `cmd` process,
  and ends with a summary line (`Ran 2 QGIS build(s): ltr qgis`) and a non-zero exit code if any build failed.

## Visual comparison across QGIS versions

`capture` renders every plugin window (dock, engine settings, run dialog empty and filled, and each Processing
tool's dialog) to PNG, in a folder named after the QGIS version, Qt version and platform:

```
qgis_plugin/tests/run_qgis_tests.sh capture ~/plugin-shots                                   (macOS/Linux)
qgis_plugin\tests\run_qgis_tests.bat C:\OSGeo4W capture C:\plugin-shots                          (Windows, all OSGeo4W builds)
qgis_plugin\tests\run_qgis_tests.bat "C:\Program Files\QGIS 4.2.3" capture C:\plugin-shots      (Windows standalone)
```

Each run rebuilds `index.html` in the output folder: one row per window, one column per captured version.
Cells are red when a window failed to open or failed a layout check (minimum size larger than 1366x768, or
clipped button/label text). Folders from different machines can be copied into one output folder and
`python qgis_plugin/tests/qgis/capture_gui.py --sheet <folder>` rebuilds the page. The command exits non-zero
when a window fails, so it can be scripted.

On macOS the Processing tool dialogs are reported as *skipped*: QGIS's own Processing parameter widgets abort
under Qt's offscreen platform there, independently of this plugin. Capture them on Linux or Windows.

GitHub Actions (`.github/workflows/qgis.yml`) runs the tests and the capture on QGIS 3.40 and 3.44 LTR (Qt5) and
4.2 (Qt6) on Linux, on the current LTR and latest QGIS on Windows (standalone installers), and on one OSGeo4W
root holding both builds, weekly and on every plugin change. The
`gui-comparison` artifact of a run contains the combined `index.html`.
