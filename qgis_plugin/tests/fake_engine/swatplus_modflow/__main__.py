import csv
import json
import os
import sys
import time
from datetime import date, timedelta

from . import VERSION


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def findings(path, data):
    out = []
    for key in ("schema_version", "name", "crs", "swatplus", "modflow", "connections", "period"):
        if key not in data:
            out.append(("ERROR", f"missing key {key}"))
    base = os.path.dirname(os.path.abspath(path))
    hru = data.get("connections", {}).get("hru_cells")
    if hru and not os.path.exists(os.path.join(base, hru)):
        out.append(("ERROR", f"connections.hru_cells not found: {hru}"))
    if "invalid" in str(data.get("name", "")):
        out.append(("ERROR", "project name asks for an invalid project"))
    if not data.get("coupling"):
        out.append(("WARNING", "no coupling section: engine defaults apply"))
    return out


def main(argv):
    if len(argv) >= 1 and argv[0] == "version":
        print(f"swatplus-modflow {VERSION}")
        return 0
    if len(argv) != 2 or argv[0] not in ("validate", "run"):
        print("usage: python -m swatplus_modflow version | validate <project> | run <project>", file=sys.stderr)
        return 1
    cmd, path = argv
    try:
        data = load(path)
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot read project: {exc}")
        return 1
    found = findings(path, data)
    for level, msg in found:
        print(f"{level}: {msg}")
    if any(level == "ERROR" for level, _ in found):
        return 1
    if cmd == "validate":
        return 0

    base = os.path.dirname(os.path.abspath(path))
    outdir = data.get("output", {}).get("directory", "results")
    outdir = outdir if os.path.isabs(outdir) else os.path.join(base, outdir)
    prefix = data.get("output", {}).get("prefix", "coupled_")
    os.makedirs(outdir, exist_ok=True)
    name = data["name"]
    start = date.fromisoformat(data["period"]["start"])
    n = 5
    delay = 0.4 if "slow" in name else float(os.environ.get("FAKE_ENGINE_DELAY", "0.02"))
    print(f"starting fake run of {name}")
    for i in range(1, n + 1):
        time.sleep(delay)
        print(f"PROGRESS {i}/{n} {(start + timedelta(days=i - 1)).isoformat()}", flush=True)
        if "failrun" in name and i == 2:
            print("RESULT failed solver did not converge", flush=True)
            return 2
    with open(os.path.join(base, data["connections"]["hru_cells"]), newline="") as fh:
        cells = [(int(r["row"]), int(r["col"])) for r in csv.DictReader(fh)]
    nrow, ncol = max(r for r, _ in cells), max(c for _, c in cells)
    with open(os.path.join(outdir, f"{prefix}daily_summary.csv"), "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["date", "recharge_m3_day"])
        for i in range(n):
            w.writerow([(start + timedelta(days=i)).isoformat(), 100.0 + i])
    with open(os.path.join(outdir, f"{prefix}final_heads.csv"), "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["layer", "row", "col", "value", "date"])
        for d in (start + timedelta(days=n - 2), start + timedelta(days=n - 1)):
            for r in range(1, nrow + 1):
                for c in range(1, ncol + 1):
                    w.writerow([1, r, c, (r * 10 + c) + (0.5 if d == start + timedelta(days=n - 1) else 0.0), d.isoformat()])
    manifest = os.path.join(outdir, "run_manifest.json")
    with open(manifest, "w") as fh:
        json.dump({"format": "swatplus-modflow-run/1", "project": name, "status": "succeeded",
                   "started": "2026-01-01T00:00:00", "finished": "2026-01-01T00:00:01",
                   "files": [{"kind": "table", "name": "daily_summary", "path": f"{prefix}daily_summary.csv",
                              "description": "Basin daily water balance", "units": "m3/day"},
                             {"kind": "cell_values", "name": "final_heads", "path": f"{prefix}final_heads.csv",
                              "description": "Heads at the last time steps", "units": "m"}]}, fh, indent=2)
    print(f"RESULT succeeded {manifest}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
