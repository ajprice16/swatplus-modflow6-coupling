#!/usr/bin/env python3
"""Check whether the local environment is ready for a coupled smoke run."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import shutil
import sys


REQUIRED_MODULES = [
    "flopy",
    "geopandas",
    "numpy",
    "pandas",
    "shapely",
    "yaml",
]

EXECUTABLE_CANDIDATES = {
    "mf6": ["mf6", "bin/mf6"],
    "swatplus": [
        "swatplus",
        "swatplus-61.0.2-ifx-lin_x86_64-Rel",
        "swatplus-61.0.1-lin-x86_64",
        "rev61.0.2_64rel.exe",
    ],
}


def _status(ok: bool) -> str:
    return "OK" if ok else "MISSING"


def check_modules() -> bool:
    print("Python modules")
    print("--------------")
    all_ok = True
    for module in REQUIRED_MODULES:
        ok = importlib.util.find_spec(module) is not None
        all_ok = all_ok and ok
        print(f"{_status(ok):8} {module}")
    print()
    return all_ok


def resolve_executable(label: str, value: str | None) -> Path | None:
    if value:
        path = Path(value).expanduser()
        if path.is_file():
            return path
        if path.is_dir():
            for candidate in EXECUTABLE_CANDIDATES.get(label, [label]):
                candidate_path = path / candidate
                if candidate_path.is_file():
                    return candidate_path
        return None

    found = shutil.which(label)
    return Path(found) if found else None


def check_executable(label: str, value: str | None) -> bool:
    resolved = resolve_executable(label, value)
    ok = resolved is not None
    shown = str(resolved) if resolved else f"{value or label} not found"
    print(f"{_status(ok):8} {label:18} {shown}")
    return ok


def check_paths(args: argparse.Namespace) -> bool:
    print("External programs")
    print("-----------------")
    all_ok = True
    all_ok &= check_executable("mf6", args.mf6)
    all_ok &= check_executable("swatplus", args.swatplus)

    toolbox_default = Path("scripts/launch_swatplus_toolbox_wine.sh")
    toolbox = Path(args.toolbox).expanduser() if args.toolbox else toolbox_default
    ok = toolbox.exists()
    all_ok &= ok
    print(f"{_status(ok):8} {'toolbox launcher':18} {toolbox}")
    print()

    print("Model inputs")
    print("------------")
    for label, value in [
        ("SWAT+ model", args.swat_model),
        ("MODFLOW model", args.modflow_model),
        ("connections", args.connections),
    ]:
        if value is None:
            print(f"{'SKIP':8} {label:18} not provided")
            continue
        path = Path(value).expanduser()
        ok = path.exists()
        all_ok &= ok
        print(f"{_status(ok):8} {label:18} {path}")
    print()
    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mf6", help="Path to MODFLOW 6 executable or install directory")
    parser.add_argument("--swatplus", help="Path to SWAT+ executable or swat_exe directory")
    parser.add_argument("--toolbox", help="Path to SWAT+ Toolbox launcher")
    parser.add_argument("--swat-model", help="Path to SWAT+ model/TxtInOut directory")
    parser.add_argument("--modflow-model", help="Path to MODFLOW 6 model directory")
    parser.add_argument("--connections", help="Path to GIS connections file")
    args = parser.parse_args()

    modules_ok = check_modules()
    paths_ok = check_paths(args)

    if modules_ok and paths_ok:
        print("Environment looks ready for a coupled smoke run.")
        return 0

    print("Environment is not fully ready yet. Fill in the MISSING items above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
