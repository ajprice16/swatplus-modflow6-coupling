"""Daily SWAT+ -> MODFLOW 6 coupling engine (libmf6 / XMI), driven by a project file."""
from .errors import EngineInputError, EngineRunError

__all__ = ["EngineInputError", "EngineRunError", "run_project", "validate_project_file"]


def __getattr__(name):
    if name == "run_project":
        from .runner import run_project
        return run_project
    if name == "validate_project_file":
        from .validate import validate_project_file
        return validate_project_file
    raise AttributeError(name)
