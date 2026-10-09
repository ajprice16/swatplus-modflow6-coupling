#!/usr/bin/env python3
"""
Parallel SWAT+ streamflow calibration framework.

Runs N SWAT+ instances simultaneously using a process pool, each evaluating a
different parameter set. Supports Latin Hypercube Sampling for initial space
exploration, then Shuffled Complex Evolution (SCE-UA via spotpy) for refinement.

Prerequisites:
    pip install spotpy pyswatplus pandas numpy

Usage:
    # LHS with 200 samples on 30 workers
    python run_swat_calibration.py \
      --swat-dir /path/to/swat/project \
      --swat-exe /path/to/swatplus/exe \
      --gauge-obs /path/to/observed/flows.csv \
      --channel-id 171 \
      --workers 30 \
      --samples 200

    # SCE-UA refinement
    python run_swat_calibration.py \
      --swat-dir /path/to/swat/project \
      --swat-exe /path/to/swatplus/exe \
      --gauge-obs /path/to/observed/flows.csv \
      --channel-id 171 \
      --workers 30 \
      --algorithm sceua \
      --iterations 1000

References:
    - Bailey et al. (2020) SWAT+MF workflow
    - Latin Hypercube Sampling (McKay et al. 1979)
    - SCE-UA (Duan et al. 1994)
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# ─── Configuration ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CalibParam:
    """Calibration parameter definition."""
    name: str       # parameter name (column in results)
    file: str       # SWAT+ input filename (relative to TxtInOut)
    column: str     # column name in that file
    lo: float       # lower bound
    hi: float       # upper bound
    mode: str = "abs"  # 'abs' = replace, 'mult' = multiply existing


def _resolve_path(arg_val, env_var: str, default: str = None) -> Path:
    """Resolve a path: CLI arg → env var → default."""
    if arg_val:
        return Path(arg_val)
    if env_var and os.environ.get(env_var):
        return Path(os.environ.get(env_var))
    if default:
        return Path(default)
    raise ValueError(f"No path provided for {env_var}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)

    # Required arguments
    ap.add_argument("--swat-dir", required=True, type=Path,
                   help="Path to SWAT+ TxtInOut directory")
    ap.add_argument("--swat-exe", required=True, type=Path,
                   help="Path to SWAT+ executable (swatplus binary)")
    ap.add_argument("--gauge-obs", required=True, type=Path,
                   help="Path to observed streamflow CSV (date, flow_m3s columns)")
    ap.add_argument("--channel-id", required=True, type=int,
                   help="SWAT+ channel gis_id at calibration gauge")

    # Calibration options
    ap.add_argument("--workers", type=int, default=8,
                   help="Number of parallel workers (default: 8)")
    ap.add_argument("--samples", type=int, default=200,
                   help="Number of LHS samples (default: 200)")
    ap.add_argument("--algorithm", choices=["lhs", "sceua"], default="lhs",
                   help="Calibration algorithm (default: lhs)")
    ap.add_argument("--iterations", type=int, default=1000,
                   help="SCE-UA iterations (default: 1000)")
    ap.add_argument("--objective", choices=["nse", "kge"], default="nse",
                   help="Objective function (default: nse)")
    ap.add_argument("--out-dir", type=Path, default=Path("./analysis/calibration"),
                   help="Output directory for results (default: ./analysis/calibration)")
    ap.add_argument("--tmp-dir", type=Path, default=None,
                   help="Temp work directory (default: SWAT_DIR/.calib_tmp)")
    ap.add_argument("--dry-run", action="store_true",
                   help="Print parameters and exit without running")

    args = ap.parse_args()

    # Validate paths
    if not args.swat_dir.exists():
        raise FileNotFoundError(f"SWAT+ directory not found: {args.swat_dir}")
    if not args.swat_exe.exists():
        raise FileNotFoundError(f"SWAT+ executable not found: {args.swat_exe}")
    if not args.gauge_obs.exists():
        raise FileNotFoundError(f"Gauge observations not found: {args.gauge_obs}")

    # Create output directory
    args.out_dir.mkdir(parents=True, exist_ok=True)

    # Set temp directory
    tmp_dir = args.tmp_dir or (args.swat_dir.parent / ".calib_tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    log.info("=" * 70)
    log.info("SWAT+ Streamflow Calibration")
    log.info("=" * 70)
    log.info(f"SWAT+ directory: {args.swat_dir}")
    log.info(f"SWAT+ executable: {args.swat_exe}")
    log.info(f"Gauge observations: {args.gauge_obs}")
    log.info(f"Channel ID: {args.channel_id}")
    log.info(f"Algorithm: {args.algorithm.upper()}")
    log.info(f"Workers: {args.workers}")
    log.info(f"Objective: {args.objective.upper()}")
    log.info(f"Output: {args.out_dir}")

    if args.dry_run:
        log.info("DRY RUN: exiting")
        return

    # TODO: Implement calibration logic
    # - Load parameter definitions
    # - Generate LHS or SCE-UA samples
    # - Run SWAT+ in parallel
    # - Evaluate objective function
    # - Sort results and save CSV

    log.info("Calibration framework initialized. Implementation pending.")


if __name__ == "__main__":
    main()
