# Software scope — what belongs in this repository

This package is **study-area agnostic**. Anything that names a watershed, a
dataset unique to one region, or a path on someone's machine belongs in a
separate application repository, not here.

## Include

| Area | Contents |
|------|----------|
| Library | `swatplus_modflow/core`, `coupling`, `interfaces`, `utils` |
| CLIs | Path-required scripts under `scripts/` |
| Examples | Toy workflows on synthetic data |
| Docs | Architecture, GIS guides, generic method notes |
| Tests | Unit + coupling-invariant tests on synthetic arrays |

## Exclude

| Area | Examples |
|------|----------|
| Path maps | Machine-specific path files or defaults |
| Regional data harvesters | State well-registry scrapers, regional aquifer-model parsers |
| Application runners | Scripts with hardcoded model dirs, gauge IDs, calibrated scenarios |
| Bulk data | Model inputs/outputs, weather archives, rasters |
| Paper material | Manuscripts, notes, logs, third-party PDFs |

## Promotion rule

When a reusable algorithm is proven in an application:

1. Extract the function/class with CLI or constructor arguments for all paths.
2. Remove default values that point at a real project or region.
3. Add or update a unit/invariant test that uses synthetic data only.
4. Document the API in README / docs — not the application results.
5. Run `pytest tests/` — including the study-area leak guard.
