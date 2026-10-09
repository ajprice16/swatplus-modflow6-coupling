"""Command-line handlers for ``build-modflow`` and ``build-connections``.

Exit codes follow docs/DATA_CONTRACTS.md: 0 success, 1 invalid spec or inputs
(nothing written), 2 the build started and failed.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from pydantic import ValidationError

from .grid import BuildInputError


def _fmt(exc: ValidationError):
    return [f"{'.'.join(str(p) for p in e['loc']) or '<spec>'}: {e['msg']}" for e in exc.errors()]


def run_build_modflow(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stdout, force=True)
    from .modflow_builder import build_modflow
    from .spec import load_spec
    try:
        spec = load_spec(args.spec)
    except FileNotFoundError:
        print(f"ERROR: build spec not found: {args.spec}")
        print("RESULT failed build spec not found")
        return 1
    except ValidationError as exc:
        for m in _fmt(exc):
            print(f"ERROR: {m}")
        print("RESULT failed invalid build spec")
        return 1
    except ValueError as exc:
        print(f"ERROR: {exc}")
        print("RESULT failed invalid build spec")
        return 1
    try:
        res = build_modflow(spec)
    except BuildInputError as exc:
        print(f"ERROR: {exc}")
        print(f"RESULT failed {' '.join(str(exc).split())[:300]}")
        return 1
    except Exception as exc:
        print(f"RESULT failed {type(exc).__name__}: {' '.join(str(exc).split())[:300]}")
        return 2
    for w in res.warnings:
        print(f"WARNING: {w}")
    print(f"RESULT succeeded {Path(res.report_path).resolve()}")
    return 0


def run_build_connections(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stdout, force=True)
    import geopandas as gpd
    from .connections import build_channel_cells, build_hru_cells, read_active_cells, write_connections
    from .grid import read_grid
    if not args.hrus and not args.channels:
        print("ERROR: give --hrus and/or --channels")
        print("RESULT failed nothing to build")
        return 1
    try:
        grid = read_grid(Path(args.grid))
        active = read_active_cells(Path(args.grid_layer), args.grid_layer_name, grid) if args.grid_layer else None
        if args.crs and args.crs.upper() != grid.crs:
            raise BuildInputError(f"--crs {args.crs} differs from grid.json crs {grid.crs}")
        hdf = hrep = cdf = crep = None
        if args.hrus:
            hdf, hrep = build_hru_cells(gpd.read_file(args.hrus), grid, id_field=args.hru_id_field,
                                        layer=args.layer, active=active, min_fraction=args.min_fraction,
                                        reproject=args.reproject)
        if args.channels:
            dem = None
            if args.direction == "dem":
                from .grid import sample_raster_to_grid
                if not args.dem:
                    raise BuildInputError("--direction dem needs --dem")
                dem = sample_raster_to_grid(Path(args.dem), grid, "average")
            cdf, crep = build_channel_cells(gpd.read_file(args.channels), grid, id_field=args.channel_id_field,
                                            layer=args.layer, active=active, direction=args.direction, dem=dem,
                                            reproject=args.reproject)
    except BuildInputError as exc:
        print(f"ERROR: {exc}")
        print(f"RESULT failed {' '.join(str(exc).split())[:300]}")
        return 1
    except Exception as exc:
        print(f"RESULT failed {type(exc).__name__}: {' '.join(str(exc).split())[:300]}")
        return 2
    if hrep and hrep["hrus_outside_grid"]:
        print(f"WARNING: {len(hrep['hrus_outside_grid'])} HRU(s) lie entirely outside the grid "
              f"(first: {hrep['hrus_outside_grid'][:5]})")
    if hrep and hrep["n_hrus_partly_outside"]:
        print(f"WARNING: {hrep['n_hrus_partly_outside']} HRU(s) are partly outside the grid; "
              "their percolation outside leaves the model")
    if crep and crep["channels_without_cells"]:
        print(f"WARNING: {len(crep['channels_without_cells'])} channel(s) have no cell "
              f"(first: {crep['channels_without_cells'][:5]})")
    write_connections(Path(args.out_dir), hdf, hrep, cdf, crep)
    print(f"RESULT succeeded {(Path(args.out_dir) / 'connections_report.json').resolve()}")
    return 0
