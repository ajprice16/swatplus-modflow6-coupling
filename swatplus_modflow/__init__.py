"""
SWAT+ and MODFLOW 6 Coupling Framework
========================================

A Python framework for bidirectional coupling of SWAT+ watershed model
with MODFLOW 6 groundwater flow model.
"""

__version__ = "0.1.0"
__author__ = "Hydrologic Coupling Team"


def __getattr__(name):
    if name == "SWATPlusMODFLOWCoupler":
        from .core.simulator import SWATPlusMODFLOWCoupler

        return SWATPlusMODFLOWCoupler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "SWATPlusMODFLOWCoupler",
    "__version__",
]
