#!/bin/bash
# Run the headless QGIS tests with the Python bundled in a QGIS installation (macOS app bundle layout).
#   QGIS_APP=/path/to/QGIS.app qgis_plugin/tests/run_qgis_tests.sh [unittest args]
# Tests use a throw-away profile folder, never the user's QGIS profile.
set -euo pipefail
: "${QGIS_APP:?set QGIS_APP to the QGIS .app bundle}"
HERE="$(cd "$(dirname "$0")" && pwd)"
A="$QGIS_APP/Contents"; R="$A/Resources"
unset PYTHONHOME PYTHONSAFEPATH
export PYTHONPATH="$R/python3.12:$R/python3.12/lib-dynload:$R/python3.12/site-packages:$R/qgis/python:$R/qgis/python/plugins:$HERE/qgis:${PYTHONPATH:-}"
export DYLD_FALLBACK_LIBRARY_PATH="$A/Frameworks"
export PROJ_DATA="$R/qgis/proj" PROJ_LIB="$R/qgis/proj" GDAL_DATA="$R/qgis/gdal"
export QT_QPA_PLATFORM=offscreen QGIS_PREFIX_PATH="$R" PYTHONDONTWRITEBYTECODE=1
cd "$HERE/qgis"
exec "$A/MacOS/python3.12" run_unittest.py "$@"
