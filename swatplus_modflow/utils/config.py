"""Configuration management"""

from pathlib import Path
from typing import Dict, Any
from dataclasses import dataclass
import yaml
import logging

logger = logging.getLogger(__name__)


@dataclass
class MODFLOWConfig:
    """MODFLOW 6 configuration"""
    executable: str = "mf6"
    working_directory: str = "./modflow"
    convergence_tol: float = 1e-4
    max_iterations: int = 10000
    output_interval: int = 1  # days


@dataclass
class SWATPlusConfig:
    """SWAT+ configuration"""
    executable: str = "swatplus"
    working_directory: str = "./swatplus"
    output_interval: int = 1  # days


@dataclass
class CouplingParametersConfig:
    """Coupling parameters"""
    recharge_delay_days: float = 10.0  # Vadose zone transfer function
    et_extinction_depth_m: float = 3.0  # ET extinction depth
    modflow_convergence_tol: float = 1e-4
    max_iterations: int = 10000
    save_state_interval_days: int = 30
    save_cell_fluxes: bool = True
    save_daily_summary: bool = True


class CouplingConfig:
    """Main coupling configuration"""
    
    def __init__(self):
        self.modflow = MODFLOWConfig()
        self.swatplus = SWATPlusConfig()
        self.coupling = CouplingParametersConfig()
    
    @classmethod
    def from_yaml(cls, config_file: str | Path) -> "CouplingConfig":
        """
        Load configuration from YAML file.
        
        Example YAML:
        ```
        modflow:
          executable: "/path/to/mf6"
          convergence_tol: 1.0e-4
          max_iterations: 10000
        
        swatplus:
          executable: "/path/to/swatplus"
        
        coupling:
          recharge_delay_days: 10.0
          et_extinction_depth_m: 3.0
        ```
        """
        config_file = Path(config_file)
        
        if not config_file.exists():
            logger.warning(f"Config file not found: {config_file}, using defaults")
            return cls()
        
        with open(config_file, 'r') as f:
            data = yaml.safe_load(f)
        
        config = cls()
        
        if 'modflow' in data:
            for key, value in data['modflow'].items():
                setattr(config.modflow, key, value)
        
        if 'swatplus' in data:
            for key, value in data['swatplus'].items():
                setattr(config.swatplus, key, value)
        
        if 'coupling' in data:
            for key, value in data['coupling'].items():
                setattr(config.coupling, key, value)
        
        logger.info(f"Loaded configuration from {config_file}")
        return config
    
    def to_yaml(self, output_file: str | Path) -> None:
        """Save configuration to YAML file"""
        data = {
            'modflow': self.modflow.__dict__,
            'swatplus': self.swatplus.__dict__,
            'coupling': self.coupling.__dict__,
        }
        
        with open(output_file, 'w') as f:
            yaml.dump(data, f, default_flow_style=False)
        
        logger.info(f"Saved configuration to {output_file}")
