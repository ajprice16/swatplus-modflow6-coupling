"""Run the headless QGIS tests from plain pytest when a QGIS installation is available.

Set QGIS_APP to the QGIS .app bundle (macOS layout); otherwise this test is skipped.
"""
import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.skipif(not os.environ.get("QGIS_APP"), reason="QGIS_APP not set; QGIS tests need a QGIS Python")
def test_headless_qgis_suite():
    proc = subprocess.run([str(HERE / "run_qgis_tests.sh")], capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
