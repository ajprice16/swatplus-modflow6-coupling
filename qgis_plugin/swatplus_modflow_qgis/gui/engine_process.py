"""Non-blocking launch of the engine CLI through QProcess.

The plugin never imports the engine: it starts ``<python> -m swatplus_modflow ...`` and reads the
documented stdout lines (PROGRESS / RESULT, docs/DATA_CONTRACTS.md section 5).
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from qgis.PyQt.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal

from ..core import engine as eng

KILL_GRACE_MS = 5000


class EngineProcess(QObject):
    """One engine command. Signals:

    * ``outputLine(str)``: every stdout/stderr line (channels are merged);
    * ``progress(done, total, date)``: parsed ``PROGRESS`` lines;
    * ``finishedRun(exit_code, status, detail)``: ``status`` is 'succeeded' / 'failed' from the last
      ``RESULT`` line, '' when the engine printed none, or 'cancelled' after ``cancel()``;
    * ``failedToStart(message)``.
    """
    outputLine = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)
    finishedRun = pyqtSignal(int, str, str)
    failedToStart = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc: Optional[QProcess] = None
        self._buffer = ""
        self._result: Optional[eng.ResultLine] = None
        self._cancelled = False
        self.lines: List[str] = []

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def start(self, program: str, args: List[str], working_dir: Optional[str] = None,
              extra_env: Optional[dict] = None) -> None:
        if self.is_running():
            raise RuntimeError("an engine process is already running")
        self._buffer, self._result, self._cancelled, self.lines = "", None, False, []
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")          # PROGRESS lines must arrive while the run is going
        env.insert("PYTHONIOENCODING", "utf-8")
        for k, v in (extra_env or {}).items():
            env.insert(k, v)
        proc.setProcessEnvironment(env)
        if working_dir:
            proc.setWorkingDirectory(working_dir)
        proc.readyReadStandardOutput.connect(self._read)
        proc.finished.connect(self._finished)
        proc.errorOccurred.connect(self._error)
        self._proc = proc
        proc.start(program, list(args))

    def cancel(self) -> None:
        """Ask the process to stop; kill it if it is still alive after a grace period."""
        if not self.is_running():
            return
        self._cancelled = True
        proc = self._proc
        proc.terminate()
        QTimer.singleShot(KILL_GRACE_MS, lambda: proc.kill() if proc.state() != QProcess.ProcessState.NotRunning else None)

    # -- slots -----------------------------------------------------------------
    def _read(self):
        data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._buffer += data.replace("\r\n", "\n").replace("\r", "\n")
        *complete, self._buffer = self._buffer.split("\n")
        for line in complete:
            self._handle(line)

    def _handle(self, line: str):
        self.lines.append(line)
        p = eng.parse_progress(line)
        if p:
            self.progress.emit(p.done, p.total, p.date or "")
        r = eng.parse_result(line)
        if r:
            self._result = r
        self.outputLine.emit(line)

    def _finished(self, exit_code, _status):
        self._read()
        if self._buffer:
            self._handle(self._buffer)
            self._buffer = ""
        if self._cancelled:
            status, detail = "cancelled", ""
        elif self._result:
            status, detail = self._result.status, self._result.detail
        else:
            status, detail = "", ""
        self.finishedRun.emit(int(exit_code), status, detail)

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.failedToStart.emit(f"could not start {self._proc.program()}: {self._proc.errorString()}")
            self.finishedRun.emit(-1, "", "")


class CommandQueue(QObject):
    """Run (program, args) commands one after another; stop at the first failure."""
    line = pyqtSignal(str)
    commandStarted = pyqtSignal(str)
    allFinished = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cmds: List[Tuple[str, List[str]]] = []
        self._proc: Optional[EngineProcess] = None
        self._ok = True
        self._extra_env = None
        self._cwd = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.is_running()

    def start(self, commands, working_dir=None, extra_env=None):
        self._cmds = list(commands)
        self._ok, self._cwd, self._extra_env = True, working_dir, extra_env
        self._next()

    def cancel(self):
        self._cmds = []
        if self._proc is not None:
            self._proc.cancel()

    def _next(self):
        if not self._cmds:
            self.allFinished.emit(self._ok)
            return
        program, args = self._cmds.pop(0)
        self.commandStarted.emit(eng.format_command(program, args))
        self._proc = EngineProcess(self)
        self._proc.outputLine.connect(self.line)
        self._proc.failedToStart.connect(self.line)
        self._proc.finishedRun.connect(self._one_done)
        self._proc.start(program, args, self._cwd, self._extra_env)

    def _one_done(self, code, status, _detail):
        if code != 0 or status in ("cancelled", "failed"):
            self._ok = False
            self.line.emit(f"command ended with exit code {code}" + (" (cancelled)" if status == "cancelled" else ""))
            self._cmds = []
        self._next()
