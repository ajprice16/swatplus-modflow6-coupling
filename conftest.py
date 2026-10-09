"""
Pytest bootstrap: put the repo root on sys.path.

The suites import project code as top-level packages (``scripts.*``,
``swatplus_modflow.*``, ``llm_calibration.*``). ``python -m pytest`` normally
prepends the cwd, but that does not happen when the interpreter runs in
safe-path mode (PYTHONSAFEPATH / -P), which is the case in at least one
environment used here -- so the same test file imported cleanly on the compute
node and failed to collect locally with ModuleNotFoundError. Making the path
explicit here removes the dependence on how pytest was invoked.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
