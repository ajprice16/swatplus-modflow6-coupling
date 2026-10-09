"""Plugin settings (QgsSettings)."""
from __future__ import annotations

from qgis.core import QgsSettings

from ..core import engine as eng


def get(key: str, default: str = "") -> str:
    return str(QgsSettings().value(key, default) or "")


def set_value(key: str, value: str) -> None:
    QgsSettings().setValue(key, value)


def engine_python() -> str:
    return get(eng.SETTINGS_PYTHON_KEY)
