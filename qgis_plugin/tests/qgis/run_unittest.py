"""unittest entry point for the headless QGIS tests.

QGIS can crash while Python tears down QgsApplication at interpreter exit, which would turn a green run
into a non-zero exit code; this runner reports the test result itself and leaves with os._exit.
"""
import os
import sys
import unittest

if __name__ == "__main__":
    args = sys.argv[1:] or ["discover", "-s", ".", "-p", "test_*.py", "-v"]
    prog = unittest.main(module=None, argv=["unittest"] + args, exit=False)
    ok = prog.result.wasSuccessful()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if ok else 1)
