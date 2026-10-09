"""Run a project end to end and write the run manifest (docs/DATA_CONTRACTS.md, section 6)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ..project import ProjectConfig
from . import driver
from .driver import MANIFEST_FORMAT
from .errors import EngineInputError, EngineRunError

log = logging.getLogger(__name__)


def _write_manifest(cfg: ProjectConfig, status: str, started: datetime, files, error: Optional[str],
                    extra: Optional[dict] = None) -> Path:
    out = cfg.output.directory
    out.mkdir(parents=True, exist_ok=True)
    data = {"format": MANIFEST_FORMAT, "project": cfg.name, "status": status,
            "started": started.isoformat(timespec="seconds"),
            "finished": datetime.now().isoformat(timespec="seconds"), "files": files}
    if error:
        data["error"] = error
    if extra:
        data["summary"] = extra
    p = out / "run_manifest.json"
    p.write_text(json.dumps(data, indent=2))
    return p


def run_project(cfg: ProjectConfig,
                progress: Optional[Callable[[int, int, "object"], None]] = None) -> Path:
    """Run the coupled simulation; returns the manifest path.

    Raises :class:`EngineInputError` if the inputs are unusable (nothing ran) and
    :class:`EngineRunError` / ``CouplingInvariantError`` if the run failed. A manifest
    with ``status: failed`` is written for the latter.
    """
    started = datetime.now()
    prep = driver.prepare_inputs(cfg)                    # EngineInputError: nothing ran
    work = cfg.output.directory / "mf6_work"
    try:
        res = driver.run_daily(prep, work, progress=progress)
    except EngineInputError:
        raise
    except Exception as exc:
        _write_manifest(cfg, "failed", started, [], f"{type(exc).__name__}: {exc}")
        raise
    summary = {k: v for k, v in res.items() if k != "files"}
    return _write_manifest(cfg, "succeeded", started, res["files"], None, summary)
