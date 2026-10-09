# SWAT+ MODFLOW 6 Coupling (QGIS plugin)

Prepares the inputs of the SWAT+ / MODFLOW 6 coupling engine (MODFLOW grid,
HRU-to-cell and channel-to-cell tables, project file) with native QGIS tools
and runs the engine as a separate process.

* Works on QGIS 3.40 and later (Qt5) and on QGIS 4 (Qt6).
* Uses only the Python bundled with QGIS; the engine runs in a Python
  environment you point the plugin at.
* Licence: MIT, see `LICENSE` in this folder (identical to the repository's).
* Documentation: `docs/QGIS_PLUGIN.md` in the repository
  (https://github.com/ajprice16/swatplus-modflow6-coupling); the file formats
  are defined in `docs/DATA_CONTRACTS.md`.
