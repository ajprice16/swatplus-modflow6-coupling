"""Install the built zip into a throw-away QGIS profile and load it through QGIS's own plugin loader."""
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import qgis_env as E

E.start_qgis()

import qgis.utils as qutils  # noqa: E402
from qgis.core import QgsApplication  # noqa: E402

sys.path.insert(0, str(E.PLUGIN_PARENT))
import build_plugin_zip  # noqa: E402

from test_plugin_gui import FakeIface  # noqa: E402

NAME = "swatplus_modflow_qgis"


class TestInstallFromZip(unittest.TestCase):
    def test_zip_installs_and_loads_in_profile(self):
        profile = Path(QgsApplication.qgisSettingsDirPath())            # the temporary profile of this run
        self.assertTrue(str(profile).startswith(tempfile.gettempdir()) or "swatplus_qgis_test_profile" in str(profile), profile)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            archive = build_plugin_zip.build(Path(td))
            plugins_dir = profile / "python" / "plugins"
            plugins_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(plugins_dir)
        # make sure the loader imports the installed copy, not the checkout used by other tests
        for mod in [m for m in sys.modules if m == NAME or m.startswith(NAME + ".")]:
            del sys.modules[mod]
        sys.path.insert(0, str(plugins_dir))
        reg = QgsApplication.processingRegistry()
        old = reg.providerById("swatplusmodflow")
        if old is not None:
            reg.removeProvider(old)
        qutils.iface = FakeIface()
        qutils.plugin_paths.append(str(plugins_dir))      # the profile folder a running QGIS scans
        try:
            qutils.updateAvailablePlugins()
            self.assertIn(NAME, qutils.available_plugins)
            self.assertEqual(qutils.pluginMetadata(NAME, "supportsQt6").lower(), "true")
            self.assertEqual(qutils.pluginMetadata(NAME, "qgisMinimumVersion"), "3.40")
            self.assertTrue(qutils.loadPlugin(NAME))
            self.assertTrue(qutils.startPlugin(NAME))
            self.assertIn(NAME, qutils.active_plugins)
            self.assertIsNotNone(reg.providerById("swatplusmodflow"))
            self.assertEqual(len(qutils.iface.docks), 1)
            self.assertTrue(str(Path(sys.modules[NAME].__file__).resolve()).startswith(str(plugins_dir.resolve())))
            self.assertTrue(qutils.unloadPlugin(NAME))
            self.assertIsNone(reg.providerById("swatplusmodflow"))
        finally:
            sys.path.remove(str(plugins_dir))
            qutils.plugin_paths.remove(str(plugins_dir))
            for mod in [m for m in sys.modules if m == NAME or m.startswith(NAME + ".")]:
                del sys.modules[mod]
            E.register_provider.__globals__["_provider"] = None
            E.register_provider()


if __name__ == "__main__":
    unittest.main()
