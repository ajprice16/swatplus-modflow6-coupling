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
    if os.environ.get("GITHUB_ACTIONS"):
        # one annotation per failure, readable through the GitHub API without the raw log
        for kind, items in (("failure", prog.result.failures), ("error", prog.result.errors)):
            for test, tb in items:
                last = " | ".join(tb.strip().splitlines()[-6:]).replace("%", "%25")
                print(f"::error title=QGIS test {kind}: {test.id()}::{last[:900]}")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if ok else 1)
