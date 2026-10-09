"""Command line: ``swatplus-modflow version | validate | run | build-modflow | build-connections``.

Exit codes (docs/DATA_CONTRACTS.md, section 5): 0 success; 1 invalid project or
inputs (nothing was run); 2 the run started and failed.
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from . import __version__


def _cmd_version(_args) -> int:
    print(f"swatplus-modflow {__version__}")
    return 0


def _cmd_validate(args) -> int:
    from .engine.validate import validate_project_file
    errors, warnings, _cfg = validate_project_file(args.project)
    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    return 1 if errors else 0


def _cmd_run(args) -> int:
    from .engine.errors import EngineInputError
    from .engine.runner import run_project
    from .engine.validate import validate_project_file
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stdout, force=True)
    errors, warnings, cfg = validate_project_file(args.project)
    for w in warnings:
        print(f"WARNING: {w}")
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        print("RESULT failed invalid project or inputs")
        sys.stdout.flush()
        return 1

    def progress(done, total, day):
        print(f"PROGRESS {done}/{total} {day.isoformat()}")
        sys.stdout.flush()

    try:
        manifest = run_project(cfg, progress=progress)
    except EngineInputError as exc:
        print(f"ERROR: {exc}")
        print(f"RESULT failed {_one_line(exc)}")
        return 1
    except Exception as exc:                                  # any failure after the run started
        logging.getLogger(__name__).error("%s: %s", type(exc).__name__, exc)
        print(f"RESULT failed {_one_line(exc)}")
        return 2
    print(f"RESULT succeeded {manifest}")
    return 0


def _one_line(exc: BaseException) -> str:
    return " ".join(f"{type(exc).__name__}: {exc}".split())


def _cmd_build_modflow(args) -> int:
    from .build.cli import run_build_modflow
    return run_build_modflow(args)


def _cmd_build_connections(args) -> int:
    from .build.cli import run_build_connections
    return run_build_connections(args)


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="swatplus-modflow", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="print the version").set_defaults(func=_cmd_version)
    v = sub.add_parser("validate", help="check a project file and its inputs")
    v.add_argument("project")
    v.set_defaults(func=_cmd_validate)
    r = sub.add_parser("run", help="run the daily coupled simulation")
    r.add_argument("project")
    r.set_defaults(func=_cmd_run)
    b = sub.add_parser("build-modflow", help="build a MODFLOW 6 model from a build spec")
    b.add_argument("spec")
    b.set_defaults(func=_cmd_build_modflow)
    c = sub.add_parser("build-connections", help="write hru_cells.csv / channel_cells.csv from GIS layers")
    c.add_argument("--grid", required=True, help="grid.json (or a grid polygon layer)")
    c.add_argument("--hrus", help="HRU polygon layer")
    c.add_argument("--channels", help="channel line layer")
    c.add_argument("--out-dir", required=True)
    c.add_argument("--hru-id-field", default="HRUS")
    c.add_argument("--channel-id-field", default="Channel")
    c.add_argument("--crs", help="expected CRS of the layers, e.g. EPSG:<code>")
    c.set_defaults(func=_cmd_build_connections)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = make_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())
