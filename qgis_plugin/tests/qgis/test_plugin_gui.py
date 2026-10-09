"""Plugin loading, dock, engine runner, run dialog and result loading (headless QGIS, fake engine)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import qgis_env as E

E.start_qgis()

from qgis.core import QgsApplication, QgsProject, QgsSettings, QgsVectorLayer  # noqa: E402
from qgis.PyQt.QtCore import QEventLoop, QTimer  # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow, QToolBar  # noqa: E402

from swatplus_modflow_qgis.core import engine as eng  # noqa: E402
from swatplus_modflow_qgis.gui import results, settings  # noqa: E402
from swatplus_modflow_qgis.gui.engine_process import CommandQueue, EngineProcess  # noqa: E402

ENGINE_PYTHON = sys.executable
os.environ["PYTHONPATH"] = str(E.FAKE_ENGINE) + os.pathsep + os.environ.get("PYTHONPATH", "")


def wait_for(signal, timeout_ms=20000):
    """Block (with the event loop running) until ``signal`` fires; return its arguments or None on timeout."""
    loop, got = QEventLoop(), []
    signal.connect(lambda *a: (got.append(a), loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return got[0] if got else None


class FakeIface:
    """The few QgisInterface members the plugin uses."""
    def __init__(self):
        self.window = QMainWindow()
        self.toolbar = QToolBar(self.window)
        self.actions, self.menu, self.docks = [], [], []

    def mainWindow(self): return self.window
    def addToolBarIcon(self, a): self.actions.append(a); self.toolbar.addAction(a)
    def removeToolBarIcon(self, a): self.actions.remove(a)
    def addPluginToMenu(self, m, a): self.menu.append((m, a))
    def removePluginMenu(self, m, a): self.menu.remove((m, a))
    def addDockWidget(self, area, dock): self.docks.append(dock)
    def removeDockWidget(self, dock): self.docks.remove(dock)


class TestPluginLifecycle(unittest.TestCase):
    def setUp(self):
        reg = QgsApplication.processingRegistry()
        old = reg.providerById("swatplusmodflow")
        if old is not None:      # another test module registered the provider; the plugin does it itself
            reg.removeProvider(old)

    def tearDown(self):
        E.register_provider()

    def test_metadata_declares_qt6_and_version_range(self):
        meta = {}
        for line in (E.PLUGIN_PARENT / "swatplus_modflow_qgis" / "metadata.txt").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                meta[k.strip()] = v.strip()
        self.assertEqual((meta["qgisMinimumVersion"], meta["qgisMaximumVersion"], meta["supportsQt6"]), ("3.40", "4.99", "True"))

    def test_class_factory_init_gui_and_unload(self):
        import swatplus_modflow_qgis as pkg
        iface = FakeIface()
        plugin = pkg.classFactory(iface)
        plugin.initGui()
        try:
            prov = QgsApplication.processingRegistry().providerById("swatplusmodflow")
            self.assertIsNotNone(prov)
            self.assertEqual(sorted(a.name() for a in prov.algorithms()),
                             ["channelconnections", "creategrid", "hruconnections", "validateinputs"])
            self.assertEqual(len(iface.actions), 1)
            self.assertFalse(iface.actions[0].icon().isNull())
            self.assertEqual(len(iface.docks), 1)
            plugin.action.setChecked(True)
            self.assertTrue(plugin.dock.isVisibleTo(plugin.dock.parent()) or not plugin.dock.isHidden())
            for alg in prov.algorithms():
                self.assertTrue(alg.shortHelpString())
        finally:
            plugin.unload()
        self.assertIsNone(QgsApplication.processingRegistry().providerById("swatplusmodflow"))
        self.assertEqual(iface.docks, [])


class TestEngineProcess(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="swatplus_qgis_")
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def project(self, name="demo", write_hru=True):
        (self.dir / "conn").mkdir(exist_ok=True)
        if write_hru:
            (self.dir / "conn" / "hru_cells.csv").write_text(
                "hru_id,layer,row,col,area_m2,fraction\n1,1,1,1,1.0,0.5\n1,1,2,3,1.0,0.5\n")
        from swatplus_modflow_qgis.core import project_file as pf
        d = pf.build_project_dict(name=name, crs=E.CRS, txtinout="swat", sim_dir="mf", libmf6="lib", hru_cells="conn/hru_cells.csv",
                                  channel_reaches="conn/sfr_reaches.csv", start="2000-01-01", end="2000-01-05",
                                  executable="swat", output_directory="out", base_dir=str(self.dir))
        path = self.dir / "project.json"
        pf.write_project_json(d, path)
        return path

    def test_version_and_validate_and_run(self):
        p = EngineProcess()
        p.start(ENGINE_PYTHON, eng.version_args())
        code, status, _ = wait_for(p.finishedRun)
        self.assertEqual(code, 0)
        self.assertIn("0.0.0-fake", "\n".join(p.lines))

        path = self.project()
        p = EngineProcess()
        p.start(ENGINE_PYTHON, eng.validate_args(str(path)))
        code, *_ = wait_for(p.finishedRun)
        errs, warns = eng.parse_findings("\n".join(p.lines))
        self.assertEqual((code, errs), (0, []))
        self.assertEqual(len(warns), 1)

        p = EngineProcess()
        seen = []
        p.progress.connect(lambda i, n, d: seen.append((i, n, d)))
        p.start(ENGINE_PYTHON, eng.run_args(str(path)), str(self.dir))
        code, status, detail = wait_for(p.finishedRun)
        self.assertEqual((code, status), (0, "succeeded"))
        self.assertEqual(seen[0], (1, 5, "2000-01-01"))
        self.assertEqual(seen[-1], (5, 5, "2000-01-05"))
        self.assertTrue(Path(detail).name == "run_manifest.json" and Path(detail).exists())

    def test_invalid_project_exit_1_and_failed_run_exit_2(self):
        p = EngineProcess()
        p.start(ENGINE_PYTHON, eng.validate_args(str(self.project("an invalid one"))))
        code, *_ = wait_for(p.finishedRun)
        self.assertEqual(code, eng.EXIT_INVALID)
        self.assertEqual(len(eng.parse_findings("\n".join(p.lines))[0]), 1)
        p = EngineProcess()
        p.start(ENGINE_PYTHON, eng.run_args(str(self.project("failrun")))) 
        code, status, detail = wait_for(p.finishedRun)
        self.assertEqual((code, status, detail), (eng.EXIT_RUN_FAILED, "failed", "solver did not converge"))

    def test_cancel_stops_a_slow_run(self):
        p = EngineProcess()
        p.start(ENGINE_PYTHON, eng.run_args(str(self.project("slow one"))))
        QTimer.singleShot(700, p.cancel)
        code, status, _ = wait_for(p.finishedRun)
        self.assertEqual(status, "cancelled")
        self.assertFalse(p.is_running())
        self.assertFalse((self.dir / "out" / "run_manifest.json").exists())

    def test_failure_to_start_is_reported(self):
        p = EngineProcess()
        msgs = []
        p.failedToStart.connect(msgs.append)
        p.start(str(self.dir / "no_such_python"), eng.version_args())
        wait_for(p.finishedRun, 5000)
        self.assertEqual(len(msgs), 1)

    def test_command_queue_stops_on_first_failure(self):
        q = CommandQueue()
        lines = []
        q.line.connect(lines.append)
        q.start([(ENGINE_PYTHON, eng.version_args()), (ENGINE_PYTHON, ["-c", "import sys; sys.exit(3)"]),
                 (ENGINE_PYTHON, eng.version_args())])
        (ok,) = wait_for(q.allFinished)
        self.assertFalse(ok)
        self.assertEqual(sum("0.0.0-fake" in l for l in lines), 1)


class TestRunDialogAndResults(unittest.TestCase):
    def setUp(self):
        E.register_provider()
        self._tmp = tempfile.TemporaryDirectory(prefix="swatplus_qgis_")
        self.dir = Path(self._tmp.name)
        settings.set_value(eng.SETTINGS_PYTHON_KEY, ENGINE_PYTHON)
        self.project = QgsProject()

    def tearDown(self):
        QgsSettings().remove(eng.SETTINGS_PYTHON_KEY)
        self._tmp.cleanup()

    def _grid_and_connections(self):
        grid, _js = E.grid_layer_from_alg(self.dir)
        self.project.addMapLayer(grid)
        E.run_alg("swatplusmodflow:hruconnections", {"HRUS": E.hru_layer(), "ID_FIELD": "HRUS", "GRID": grid, "LAYER": 1,
                                                    "OUTPUT": str(self.dir / "hru_cells.csv")})
        (self.dir / "sfr_reaches.csv").write_text("rno,channel_id,seq,layer,row,col,length_m\n")
        return grid

    def _fill(self, dlg, name="dialog demo"):
        dlg.project_path.setText(str(self.dir / "project.json"))
        dlg.name.setText(name)
        dlg.crs.setText(E.CRS)
        dlg.txtinout.setText(str(self.dir / "swat"))
        dlg.executable.setText("swat-exe")
        dlg.sim_dir.setText(str(self.dir / "mf"))
        dlg.libmf6.setText(str(self.dir / "libmf6.so"))
        dlg.hru_cells.setText(str(self.dir / "hru_cells.csv"))
        dlg.channel_reaches.setText(str(self.dir / "sfr_reaches.csv"))
        from qgis.PyQt.QtCore import QDate
        dlg.start.setDate(QDate(2000, 1, 1))
        dlg.end.setDate(QDate(2000, 1, 31))
        dlg.out_dir.setText(str(self.dir / "results"))

    def test_full_run_writes_valid_project_and_loads_results(self):
        from swatplus_modflow_qgis.gui.run_dialog import RunDialog
        grid = self._grid_and_connections()
        dlg = RunDialog(FakeIface(), None, self.project)
        self._fill(dlg)
        dlg.grid_combo.setLayer(grid)
        dlg.run()
        self.assertTrue(dlg.proc is not None)
        wait_for(dlg.proc.finishedRun, 30000)
        data = json.loads((self.dir / "project.json").read_text())
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["output"]["directory"], "results")
        self.assertIn("Run succeeded", dlg.status.text(), dlg.log.toPlainText())
        self.assertEqual((dlg.progress.value(), dlg.progress.maximum()), (5, 5))
        self.assertTrue(dlg.loaded_layers, dlg.log.toPlainText())
        names = sorted(l.name() for l in dlg.loaded_layers)
        self.assertEqual(names, ["daily_summary [m3/day]", "final_heads [m]"])
        table = next(l for l in dlg.loaded_layers if l.name().startswith("daily"))
        self.assertEqual(table.featureCount(), 5)
        self.assertEqual(table.fields().names(), ["date", "recharge_m3_day"])
        heads = next(l for l in dlg.loaded_layers if l.name().startswith("final"))
        self.assertEqual(heads.featureCount(), E.NROW * E.NCOL)
        self.assertTrue(heads.isSpatial() and not table.isSpatial())
        for f in heads.getFeatures():       # latest date only, value joined on row/col
            self.assertAlmostEqual(f["value"], f["row"] * 10 + f["col"] + 0.5)
            self.assertEqual(f["date"], "2000-01-05")
        # nw cell is row 1 / col 1 and sits at the north-west of the grid
        nw = next(f for f in heads.getFeatures() if f["row"] == 1 and f["col"] == 1)
        bb = nw.geometry().boundingBox()
        self.assertEqual((bb.xMinimum(), bb.yMaximum()), (E.X0, E.Y0 + E.NROW * E.D))
        self.assertTrue((self.dir / "results" / "final_heads_L1.gpkg").exists())
        group = self.project.layerTreeRoot().children()[0]
        self.assertTrue(group.name().startswith("SWAT+ MODFLOW 6 results"))
        dlg.close()

    def test_failed_run_and_progress_ui(self):
        from swatplus_modflow_qgis.gui.run_dialog import RunDialog
        self._grid_and_connections()
        dlg = RunDialog(FakeIface(), None, self.project)
        self._fill(dlg, "failrun demo")
        dlg.run()
        wait_for(dlg.proc.finishedRun, 30000)
        self.assertIn("Run failed", dlg.status.text())
        self.assertIn("solver did not converge", dlg.status.text())
        self.assertEqual(dlg.loaded_layers, [])
        self.assertTrue(dlg.run_button.isEnabled())
        dlg.close()

    def test_cancel_from_dialog(self):
        from swatplus_modflow_qgis.gui.run_dialog import RunDialog
        self._grid_and_connections()
        dlg = RunDialog(FakeIface(), None, self.project)
        self._fill(dlg, "slow demo")
        dlg.run()
        self.assertFalse(dlg.run_button.isEnabled())
        self.assertTrue(dlg.cancel_button.isEnabled())
        QTimer.singleShot(700, dlg.cancel)
        wait_for(dlg.proc.finishedRun, 30000)
        self.assertEqual(dlg.status.text(), "Cancelled")
        self.assertTrue(dlg.run_button.isEnabled())
        dlg.close()

    def test_validate_reports_findings(self):
        from swatplus_modflow_qgis.gui.run_dialog import RunDialog
        self._grid_and_connections()
        dlg = RunDialog(FakeIface(), None, self.project)
        self._fill(dlg, "invalid demo")
        dlg.validate()
        wait_for(dlg.proc.finishedRun, 30000)
        self.assertIn("1 error(s)", dlg.status.text())
        dlg.close()

    def test_incomplete_form_does_not_write(self):
        from swatplus_modflow_qgis.gui.run_dialog import RunDialog
        from qgis.PyQt import QtWidgets
        shown = []
        QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: shown.append(a[2]))
        dlg = RunDialog(FakeIface(), None, self.project)
        dlg.project_path.setText(str(self.dir / "p.json"))
        dlg.write_project()
        self.assertFalse((self.dir / "p.json").exists())
        self.assertTrue(shown and "incomplete" in shown[0])
        dlg.close()

    def test_load_project_roundtrip_into_form(self):
        from swatplus_modflow_qgis.gui.run_dialog import RunDialog
        dlg = RunDialog(FakeIface(), None, self.project)
        self._fill(dlg, "roundtrip")
        dlg.recharge_update.setCurrentText("daily")
        dlg.pumping_scale.setValue(0.0)
        self.assertIsNotNone(dlg.write_project())
        dlg2 = RunDialog(FakeIface(), None, self.project)
        dlg2.project_path.setText(str(self.dir / "project.json"))
        dlg2.load_project()
        self.assertEqual(dlg2.project_dict(), dlg.project_dict())
        self.assertEqual(json.loads((self.dir / "project.json").read_text())["coupling"], {"recharge_update": "daily", "pumping_scale": 0.0})
        dlg.close(); dlg2.close()

    def test_settings_dialog_check_engine_and_commands(self):
        from swatplus_modflow_qgis.gui.settings_dialog import EngineSettingsDialog
        dlg = EngineSettingsDialog()
        dlg.python_edit.setText(ENGINE_PYTHON)
        dlg.check_engine()
        wait_for(dlg.queue.allFinished, 20000)
        self.assertIn("0.0.0-fake", dlg.log.toPlainText())
        self.assertTrue(dlg.log.toPlainText().rstrip().endswith("OK"))
        dlg.base_python.setText("/some/python")
        dlg.venv_dir.setText(str(self.dir / "venv"))
        dlg.package.setText("/some/package")
        text = dlg.commands_view.toPlainText()
        self.assertIn("-m venv", text)
        self.assertIn("pip install /some/package", text)
        dlg.save()
        self.assertEqual(settings.engine_python(), ENGINE_PYTHON)
        dlg.close()


if __name__ == "__main__":
    unittest.main()
