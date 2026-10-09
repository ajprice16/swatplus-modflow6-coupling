#!/bin/bash
# Run the headless QGIS tests, or capture GUI screenshots, with the Python of a QGIS installation.
#
#   qgis_plugin/tests/run_qgis_tests.sh [unittest args]           run the tests
#   qgis_plugin/tests/run_qgis_tests.sh capture <out folder>       screenshots + <out>/index.html
#
# Which QGIS is used:
#   QGIS_APP=/path/to/QGIS.app        macOS app bundle
#   QGIS_FLATPAK=1                    Linux Flatpak org.qgis.qgis (flatpak run --command=python3)
#   (neither)                         the system python3, which must be able to import qgis.core
#                                     (Linux packages, the qgis/qgis Docker images)
# Windows: use run_qgis_tests.bat. Tests use a throw-away profile folder, never the user's QGIS profile.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" PYTHONDONTWRITEBYTECODE=1

if [ "${1:-}" = "capture" ]; then
  [ -n "${2:-}" ] || { echo "usage: $0 capture <out folder>" >&2; exit 2; }
  mkdir -p "$2"; OUT="$(cd "$2" && pwd)"
  set -- capture_gui.py --out "$OUT"
else
  set -- run_unittest.py "$@"
fi
cd "$HERE/qgis"

if [ -n "${QGIS_APP:-}" ]; then
  A="$QGIS_APP/Contents"; R="$A/Resources"
  PY=$(ls "$A"/MacOS/python3.* 2>/dev/null | head -n 1)
  PYV=$(basename "$PY"); PYV=${PYV#python}
  unset PYTHONHOME PYTHONSAFEPATH
  export PYTHONPATH="$R/python$PYV:$R/python$PYV/lib-dynload:$R/python$PYV/site-packages:$R/qgis/python:$R/qgis/python/plugins:$HERE/qgis:${PYTHONPATH:-}"
  export DYLD_FALLBACK_LIBRARY_PATH="$A/Frameworks"
  export PROJ_DATA="$R/qgis/proj" PROJ_LIB="$R/qgis/proj" GDAL_DATA="$R/qgis/gdal" QGIS_PREFIX_PATH="$R"
  exec "$PY" "$@"
elif [ -n "${QGIS_FLATPAK:-}" ]; then
  # The sandbox sees the home directory; the repository must live under it.
  exec flatpak run --env=QT_QPA_PLATFORM="$QT_QPA_PLATFORM" --env=PYTHONDONTWRITEBYTECODE=1 \
       --filesystem="$HERE/../.." --cwd="$HERE/qgis" --command=python3 org.qgis.qgis "$@"
else
  python3 -c "import qgis.core" 2>/dev/null || {
    echo "python3 cannot import qgis.core: set QGIS_APP (macOS) or QGIS_FLATPAK=1, or install QGIS" >&2; exit 2; }
  exec python3 "$@"
fi
