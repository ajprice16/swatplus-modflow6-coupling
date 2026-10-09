# SWAT+ ↔ MODFLOW 6 Coupling — Package Instructions

This repository is a **location-agnostic** coupling package. It must work for
any watershed and must not contain anything specific to a particular study
area.

Architectural and hydraulic integrity rules (mass balance, temporal sync, CRS
alignment) live in `.cursorrules` — the single source of truth for coupling
physics.

## Scope

**In scope**
- `swatplus_modflow/` library (core, coupling, interfaces, utils)
- Generic CLIs under `scripts/` that take all model paths as arguments
- Examples with synthetic data, GIS docs, unit and invariant tests
- `config.example.yaml`

**Out of scope**
- Watershed names, gauge numbers, CRS codes, grid dimensions or coordinates
  used as defaults, examples or test fixtures
- Regional datasets (well databases, regional aquifer models, weather caches)
- Calibrated SWAT+ projects, MODFLOW scenario directories, model output
- Absolute paths to any machine; manuscripts, notes or presentation material
- Third-party PDFs (cite them in docs instead)

`tests/test_no_study_area_content.py` enforces the most common leaks.

## Live vs facade API

- Documented entry point: the daily libmf6 engine, `swatplus-modflow run project.yaml`
  (`swatplus_modflow/engine/`). `SWATPlusMODFLOWCoupler` is a deprecated facade whose
  file-based MODFLOW interface raises (it cannot apply daily forcing).
- CLIs must remain path-parameterized.
- Code promoted from an application project must have every study default
  (model names, gauge IDs, output dir names, paths) removed before merging.

Tests: `pytest tests/ -v`
