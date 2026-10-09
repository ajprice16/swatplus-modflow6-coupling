"""Make the plugin package importable from the repository checkout."""
import sys
from pathlib import Path

PLUGIN_PARENT = Path(__file__).resolve().parents[1]
REPO = PLUGIN_PARENT.parent
for p in (PLUGIN_PARENT, REPO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
