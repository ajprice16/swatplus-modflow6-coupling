#!/usr/bin/env python3
"""Build an installable QGIS plugin archive (standard library only).

    python qgis_plugin/build_plugin_zip.py [--out DIR]

The archive's single top-level folder is ``swatplus_modflow_qgis/`` (what QGIS's "Install from ZIP"
expects). Tests, caches and editor files are left out. The file name carries the version from
metadata.txt. Entries have fixed timestamps and sorted order, so the same sources give the same bytes.
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

PLUGIN_NAME = "swatplus_modflow_qgis"
HERE = Path(__file__).resolve().parent
SRC = HERE / PLUGIN_NAME
EXCLUDE_DIRS = {"__pycache__", "tests", "test", ".git", ".pytest_cache", ".mypy_cache"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".orig", ".rej", ".swp"}
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db"}
REQUIRED_METADATA = ("name", "qgisMinimumVersion", "description", "version", "author", "repository")
FIXED_TIME = (2020, 1, 1, 0, 0, 0)


def read_metadata(path: Path) -> dict:
    meta = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "[")) and "=" in line:
            key, value = line.split("=", 1)
            meta[key.strip()] = value.strip()
    return meta


def plugin_files(src: Path = SRC):
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if not p.is_file() or EXCLUDE_DIRS.intersection(rel.parts):
            continue
        if p.suffix in EXCLUDE_SUFFIXES or p.name in EXCLUDE_NAMES:
            continue
        yield p, rel


def build(out_dir: Path, src: Path = SRC) -> Path:
    meta = read_metadata(src / "metadata.txt")
    missing = [k for k in REQUIRED_METADATA if not meta.get(k)]
    if missing:
        raise SystemExit(f"metadata.txt lacks: {', '.join(missing)}")
    for need in ("__init__.py", "icon.svg", "LICENSE"):
        if not (src / need).exists():
            raise SystemExit(f"{need} is missing from {src}")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{PLUGIN_NAME}-{meta['version']}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, rel in plugin_files(src):
            info = zipfile.ZipInfo(f"{PLUGIN_NAME}/{rel.as_posix()}", FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    return target


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(HERE / "build"), help="output folder (default: qgis_plugin/build)")
    args = ap.parse_args(argv)
    target = build(Path(args.out))
    with zipfile.ZipFile(target) as zf:
        print(f"{target} ({len(zf.namelist())} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
