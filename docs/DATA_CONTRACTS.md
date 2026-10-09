# Data contracts between the GIS front end and the coupling engine

The QGIS plugin (or any other front end) and the engine are separate programs
that exchange files. The formats below are the whole interface. A front
end that writes them correctly can drive the engine without importing it, and
the engine never needs QGIS.

Conventions shared by every file:

* MODFLOW indices (`layer`, `row`, `col`) are **1-based**. Row 1 is the
  northernmost row, column 1 the westernmost (MODFLOW convention).
* Lengths in metres, areas in m², in the project CRS, which must be projected.
* CSV files are UTF-8, comma-separated, with a header row. Extra columns are
  allowed and ignored by readers that do not know them.
* Every format carries a version. JSON files state it in a `format` key and
  readers refuse unknown major versions. CSV files have no version line; a
  table's version is implied by its header columns. Never add comment lines.
* Float columns are written at full precision. Rounding fractions, e.g. to
  6 decimals, loses water.

## 1. Project file — `project.yaml` (schema_version 1)

Defined by `swatplus_modflow/project.py`; JSON Schema in
[`project_schema.json`](project_schema.json); annotated example in
`project.example.yaml`. Front ends without PyYAML may write it as JSON: JSON is
valid YAML.

## 2. MODFLOW grid — `grid.json` + grid polygon layer

`grid.json`:

```json
{
  "format": "swatplus-modflow-grid/1",
  "crs": "EPSG:<code>",
  "xorigin": 0.0,
  "yorigin": 0.0,
  "angrot": 0.0,
  "delr": 500.0,
  "delc": 500.0,
  "nrow": 40,
  "ncol": 60
}
```

* `xorigin`, `yorigin`: the **lower-left** corner of the grid, as in MF6 DIS
  `XORIGIN`/`YORIGIN`.
* `delr`: column width (x direction); `delc`: row height (y direction).
  Version 1 grids are uniform and unrotated (`angrot` must be 0).
* The upper-left corner is `(xorigin, yorigin + nrow*delc)`. Cell `(row, col)`
  spans x in `[xorigin + (col-1)*delr, xorigin + col*delr]` and y in
  `[ytop - row*delc, ytop - (row-1)*delc]`.

Grid polygon layer (GeoPackage, one polygon per cell): integer fields `row`,
`col` and `active`. `active` is 1 when the cell overlaps the model domain with
positive area; cells that only touch the domain boundary are 0.

## 3. HRU ↔ cell table — `hru_cells.csv` (format `swatplus-modflow-hru-cells/1`)

| column | type | meaning |
|---|---|---|
| `hru_id` | int | SWAT+ HRU id (the `id` column of `hru.con`) |
| `layer` | int | MODFLOW layer that receives the recharge (usually 1) |
| `row`, `col` | int | cell |
| `area_m2` | float | area of the HRU inside this cell |
| `fraction` | float | `area_m2` / total polygon area of that HRU |

For an HRU completely inside the grid, its fractions sum to 1. Readers
report HRUs whose fractions sum to less than 1, since that water leaves the
model, rather than silently rescaling them.

The engine converts SWAT+ percolation (mm/day over the `hru.con` area) to
m³/day as `perc_mm * 1e-3 * area_hru_con_m2 * fraction`. It warns when an
HRU's whole-polygon area, `sum(area_m2) / sum(fraction)`, and its `hru.con`
area differ by more than 5%.

`hru_id` and `channel_id` are the `id` column of `hru.con` / `chandeg.con`.
SWAT+ output rows are matched to them through the output `unit` column, with
`gis_id` as a fallback when there is no `unit` column.

## 4. Channel ↔ cell table — `channel_cells.csv` (format `swatplus-modflow-channel-cells/1`)

| column | type | meaning |
|---|---|---|
| `channel_id` | int | SWAT+ channel id (`id` in `chandeg.con`, `Channel` in QSWAT+ `rivs1`) |
| `seq` | int | 1-based order of this piece along the channel, from the upstream end |
| `layer`, `row`, `col` | int | cell |
| `length_m` | float | channel length inside this cell |
| `fraction` | float | `length_m` / total channel length |

A channel that leaves and re-enters a cell has one row per contiguous piece,
so `(channel_id, row, col)` may repeat with different `seq`. Pieces outside the
grid have no row, so a channel's fractions can sum to less than 1. Readers
warn and do not rescale. The engine spreads such a channel's whole local SWAT+
flow over its in-grid reaches, in proportion to length, so SWAT+ channel water
is conserved. This differs from HRUs, whose off-grid percolation leaves the
model.

The MODFLOW builder turns each row into one SFR reach (or RIV cell) and writes
the reach ↔ channel map it creates, `sfr_reaches.csv` (`rno`, `channel_id`,
`seq`, `layer`, `row`, `col`, `length_m`), with `rno` **1-based** as in the
MF6 SFR input. The project file's `connections.channel_reaches` points at that
file, and `validate` reports an error telling the user to run `build-modflow`
when it is missing. The optional `connections.channel_cells` and
`connections.grid` fields record the front end's files, which `validate`
cross-checks against the model.

## 4a. Well table — `well_cells.csv` (optional)

| column | type | meaning |
|---|---|---|
| `layer`, `row`, `col` | int | cell |
| `rate_m3d` | float | MODFLOW sign convention: negative = extraction | Channel topology
(downstream links) is read from the SWAT+ inputs, not from this table.

## 5. Engine command line

```
python -m swatplus_modflow version
python -m swatplus_modflow validate <project.yaml>
python -m swatplus_modflow run <project.yaml>
```

(The `swatplus-modflow` console script is identical.)

* Exit codes: `0` success, `1` invalid project or inputs (nothing was run),
  `2` the run started and failed.
* `validate` prints one problem per line, prefixed `ERROR:` or `WARNING:`.
* `run` writes log lines to stdout. Progress lines are machine readable:
  `PROGRESS <done>/<total> <YYYY-MM-DD>`. The last line is
  `RESULT succeeded <absolute manifest path>` or `RESULT failed <reason>`.
* `run` never builds the MODFLOW model; run `build-modflow <build_spec.yaml>`
  first. `build-connections` writes the tables in sections 3 and 4.

## 6. Run manifest — `<output.directory>/run_manifest.json`

```json
{
  "format": "swatplus-modflow-run/1",
  "project": "<project name>",
  "status": "succeeded",
  "started": "2026-01-01T00:00:00",
  "finished": "2026-01-01T00:10:00",
  "files": [
    {"kind": "table", "name": "daily_summary", "path": "coupled_daily_summary.csv",
     "description": "Basin daily water balance", "units": "m3/day"},
    {"kind": "cell_values", "name": "final_heads", "path": "coupled_final_heads.csv",
     "description": "Heads at the last time step", "units": "m"}
  ]
}
```

* `path` is relative to the manifest's folder.
* `kind: table`: any CSV, loaded as a table.
* `kind: cell_values`: CSV with columns `layer,row,col,value` and optionally
  `date`, the ISO date of the time step the values refer to. Without `date`,
  the values are for the final step. A front end joins it to the grid layer on
  `row`,`col`.
