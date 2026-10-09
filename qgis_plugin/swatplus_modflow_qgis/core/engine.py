"""Command lines and output parsing for the engine CLI (docs/DATA_CONTRACTS.md, section 5)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

ENGINE_MODULE = "swatplus_modflow"
SETTINGS_PYTHON_KEY = "swatplus_modflow_qgis/engine_python"
SETTINGS_BASE_PYTHON_KEY = "swatplus_modflow_qgis/venv_base_python"
SETTINGS_PACKAGE_SPEC_KEY = "swatplus_modflow_qgis/package_spec"
SETTINGS_VENV_DIR_KEY = "swatplus_modflow_qgis/venv_dir"

EXIT_OK, EXIT_INVALID, EXIT_RUN_FAILED = 0, 1, 2

_PROGRESS_RE = re.compile(r"^PROGRESS\s+(\d+)\s*/\s*(\d+)(?:\s+(\S+))?\s*$")
_RESULT_RE = re.compile(r"^RESULT\s+(succeeded|failed)(?:\s+(.*\S))?\s*$")
_FINDING_RE = re.compile(r"^(ERROR|WARNING):\s*(.*)$")


def version_args() -> List[str]:
    return ["-m", ENGINE_MODULE, "version"]


def validate_args(project_path: str) -> List[str]:
    return ["-m", ENGINE_MODULE, "validate", project_path]


def run_args(project_path: str) -> List[str]:
    return ["-m", ENGINE_MODULE, "run", project_path]


def format_command(program: str, args: List[str]) -> str:
    """Human-readable command line (for display only; processes are started with an argument list)."""
    def q(s: str) -> str:
        return f'"{s}"' if re.search(r"\s", s) else s
    return " ".join(q(x) for x in [program] + list(args))


@dataclass
class ProgressLine:
    done: int
    total: int
    date: Optional[str]


@dataclass
class ResultLine:
    status: str        # "succeeded" | "failed"
    detail: str        # manifest path when succeeded, reason when failed


def parse_progress(line: str) -> Optional[ProgressLine]:
    m = _PROGRESS_RE.match(line.strip())
    if not m:
        return None
    done, total = int(m.group(1)), int(m.group(2))
    if total <= 0 or done > total:
        return None
    return ProgressLine(done, total, m.group(3))


def parse_result(line: str) -> Optional[ResultLine]:
    m = _RESULT_RE.match(line.strip())
    if not m:
        return None
    return ResultLine(m.group(1), (m.group(2) or "").strip())


def parse_findings(text: str) -> Tuple[List[str], List[str]]:
    """Split ``validate`` output into (errors, warnings)."""
    errors, warnings = [], []
    for raw in text.splitlines():
        m = _FINDING_RE.match(raw.strip())
        if m:
            (errors if m.group(1) == "ERROR" else warnings).append(m.group(2).strip())
    return errors, warnings


def venv_python(venv_dir: str, windows: Optional[bool] = None) -> str:
    """Interpreter inside a virtual environment folder."""
    if windows is None:
        windows = os.name == "nt"
    return os.path.join(venv_dir, "Scripts", "python.exe") if windows else os.path.join(venv_dir, "bin", "python")


def venv_commands(base_python: str, venv_dir: str, package_spec: str,
                  windows: Optional[bool] = None) -> List[Tuple[str, List[str]]]:
    """The exact commands the 'create environment' helper runs, as (program, args) pairs.

    ``package_spec`` is a local project folder or archive, or a URL / VCS requirement
    that pip understands. It is passed as a single argument (no shell is involved).
    """
    package_spec = package_spec.strip()
    if not package_spec or package_spec.startswith("-"):
        raise ValueError("package must be a local path or URL, not an option")
    py = venv_python(venv_dir, windows)
    return [
        (base_python, ["-m", "venv", venv_dir]),
        (py, ["-m", "pip", "install", "--upgrade", "pip"]),
        (py, ["-m", "pip", "install", package_spec]),
    ]
