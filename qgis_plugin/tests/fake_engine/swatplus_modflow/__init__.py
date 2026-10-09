"""FAKE engine used only to test the QGIS plugin: it obeys the CLI contract of docs/DATA_CONTRACTS.md
(section 5 and 6) without any hydrology. Put this folder on PYTHONPATH and run
``python -m swatplus_modflow version|validate|run``.

Behaviour switches, taken from the project's ``name``:
  contains 'invalid' : validate and run report an ERROR (exit 1);
  contains 'failrun' : run stops after two steps with 'RESULT failed' (exit 2);
  contains 'slow'    : 0.4 s per step (to test cancelling).
"""
VERSION = "0.0.0-fake"
