#!/usr/bin/env python3
"""Convert a MODFLOW-2005 STR package into a RIV package of an existing MF6 model.

Example::

    python scripts/convert_str_to_riv.py \\
        --str-file /path/to/model.str \\
        --mf6-ws /path/to/mf6_model_dir \\
        [--model-name gwf] [--fixed-format] [--dry-run]

See ``swatplus_modflow.utils.str_to_riv`` for what the conversion keeps and
what it drops (routing, flow limits, diversions).
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from swatplus_modflow.utils.str_to_riv import parse_str_file, str_to_riv_spd

logger = logging.getLogger("convert_str_to_riv")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--str-file", type=Path, required=True, help="MODFLOW-2005 STR input file")
    ap.add_argument("--mf6-ws", type=Path, required=True, help="Directory of the MF6 simulation to update")
    ap.add_argument("--model-name", default=None,
                    help="GWF model name inside the simulation (default: the only model present)")
    ap.add_argument("--fixed-format", action="store_true", help="STR item 6/7 records are fixed-column")
    ap.add_argument("--dry-run", action="store_true", help="Parse and report only; do not write MF6 files")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    import flopy  # imported late so --help works without flopy

    sim = flopy.mf6.MFSimulation.load(sim_ws=str(args.mf6_ws), verbosity_level=0)
    names = list(sim.model_names)
    if args.model_name is None:
        if len(names) != 1:
            ap.error(f"simulation has models {names}; pass --model-name")
        model_name = names[0]
    else:
        model_name = args.model_name
        if model_name not in names:
            ap.error(f"model {model_name!r} not in simulation (found {names})")
    gwf = sim.get_model(model_name)
    nper = sim.tdis.nper.get_data()

    pkg = parse_str_file(args.str_file, fixed_format=args.fixed_format, nper=nper)
    spd = str_to_riv_spd(pkg)

    nlay, nrow, ncol = gwf.modelgrid.nlay, gwf.modelgrid.nrow, gwf.modelgrid.ncol
    for per, recs in spd.items():
        for (k, i, j), *_ in recs:
            if not (0 <= k < nlay and 0 <= i < nrow and 0 <= j < ncol):
                raise SystemExit(
                    f"period {per + 1}: STR cell ({k + 1},{i + 1},{j + 1}) is outside the "
                    f"MF6 grid ({nlay}x{nrow}x{ncol})"
                )

    seg_counts = Counter(r.segment for r in pkg.periods[0])
    logger.info("STR: %d segments, %d reaches in period 1, %d stress periods",
                len(seg_counts), len(pkg.periods[0]), pkg.nper)
    if args.dry_run:
        return 0

    if gwf.get_package("riv") is not None:
        logger.warning("Replacing existing RIV package in model %s", model_name)
        gwf.remove_package("riv")
    # Every period is written explicitly: an omitted MF6 period would keep the
    # previous period's rivers active, whereas STR ITMP=0 switches them off.
    maxbound = max((len(r) for r in spd.values()), default=0)
    if maxbound == 0:
        raise SystemExit("STR file has no active reaches in any stress period")
    flopy.mf6.ModflowGwfriv(gwf, stress_period_data=spd, maxbound=maxbound, save_flows=True)
    sim.write_simulation()
    logger.info("Wrote RIV package to %s", args.mf6_ws)
    return 0


if __name__ == "__main__":
    sys.exit(main())
