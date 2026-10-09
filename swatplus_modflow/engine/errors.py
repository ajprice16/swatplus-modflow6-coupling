"""Exception types that map onto the command-line exit codes (docs/DATA_CONTRACTS.md, section 5)."""
from __future__ import annotations


class EngineInputError(ValueError):
    """The project or one of its inputs is invalid. Nothing was run (exit code 1)."""


class EngineRunError(RuntimeError):
    """The run started and failed (exit code 2)."""
