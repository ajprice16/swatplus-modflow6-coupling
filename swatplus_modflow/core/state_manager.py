"""Simulation state management"""

from datetime import datetime
from typing import Dict, Any
import numpy as np
import pandas as pd
from pathlib import Path


class SimulationState:
    """Tracks simulation state across timesteps"""
    
    def __init__(self):
        self.dates = []
        self.modflow_heads = {}  # cell -> [head values]
        self.fluxes = {
            'recharge': [],
            'et': [],
            'pumping': [],
            'baseflow': []
        }
    
    def initialize(self, start_date: datetime, modflow_heads: Dict[int, float]) -> None:
        """Initialize state at simulation start"""
        self.dates = [start_date]
        for cell_id, head in modflow_heads.items():
            self.modflow_heads[cell_id] = [head]
    
    def update(
        self,
        date: datetime,
        modflow_heads: Dict[int, float],
        recharge: Dict[int, float],
        et: Dict[int, float],
        pumping: Dict[int, float],
        baseflow: Dict[int, float]
    ) -> None:
        """Update state for current timestep"""
        self.dates.append(date)
        
        for cell_id, head in modflow_heads.items():
            if cell_id not in self.modflow_heads:
                self.modflow_heads[cell_id] = []
            self.modflow_heads[cell_id].append(head)
        
        self.fluxes['recharge'].append(sum(recharge.values()))
        self.fluxes['et'].append(sum(et.values()))
        self.fluxes['pumping'].append(sum(pumping.values()))
        self.fluxes['baseflow'].append(sum(baseflow.values()))
    
    def get_heads_dataframe(self) -> pd.DataFrame:
        """Get heads as DataFrame"""
        df = pd.DataFrame({'date': self.dates})
        for cell_id, heads in self.modflow_heads.items():
            df[f'cell_{cell_id}'] = heads
        return df
    
    def save_to_hdf5(self, filepath: Path) -> None:
        """Save state to HDF5 file"""
        df = self.get_heads_dataframe()
        df.to_hdf(filepath, key='heads', mode='w')
        
        # Save fluxes
        df_flux = pd.DataFrame({'date': self.dates, **self.fluxes})
        df_flux.to_hdf(filepath, key='fluxes', mode='a')
