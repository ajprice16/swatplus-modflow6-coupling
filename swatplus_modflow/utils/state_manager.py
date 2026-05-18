"""
State management for SWAT+ - MODFLOW 6 coupling simulations.

Handles:
- Saving/loading coupling state to HDF5
- Tracking water balance components
- Managing vadose zone transfer function state
- Checkpoint creation and restoration
"""

from typing import Dict, Any, Optional
from datetime import datetime
from pathlib import Path
import h5py
import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)


class CouplingStateManager:
    """
    Manages state persistence for SWAT+ - MODFLOW 6 coupled simulations.
    
    Tracks:
    - MODFLOW groundwater heads (by cell)
    - SWAT+ soil water storage (by HRU)
    - Vadose zone recharge delay state (by HRU)
    - Water balance components (cumulative)
    - Exchange fluxes (daily)
    
    State file format: HDF5
    - /timesteps/daily_fluxes (DataFrame with columns: date, cell_id, flux_type, value_m3_day)
    - /timesteps/water_balance (DataFrame with columns: date, component, value_m3)
    - /state/heads (Dict[int, float]: cell_id -> head_m)
    - /state/soil_water (Dict[int, float]: hru_id -> sw_mm)
    - /state/recharge_delay (Dict[int, Dict]: hru_id -> {R_prev, days})
    - /metadata (attributes: start_date, end_date, timestep_days)
    """
    
    def __init__(self, output_dir: Path):
        """
        Initialize state manager.
        
        Args:
            output_dir: Directory for state files
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # In-memory tracking
        self.water_balance = []  # List of {date, component, value_m3}
        self.daily_fluxes = []   # List of {date, cell_id, flux_type, value_m3_day}
        
        self.logger = logging.getLogger(__name__)
    
    def save_to_hdf5(
        self,
        filepath: Path,
        current_date: datetime,
        heads: Dict[int, float],
        soil_water: Dict[int, float],
        recharge_delay_state: Dict[int, Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Save coupling state to HDF5 file.
        
        Args:
            filepath: Path to HDF5 file
            current_date: Current simulation date
            heads: MODFLOW cell heads (cell_id -> head_m)
            soil_water: SWAT+ soil water (hru_id -> sw_mm)
            recharge_delay_state: Vadose zone state (hru_id -> {R_prev, days})
            metadata: Optional simulation metadata
        """
        self.logger.info(f"Saving state to {filepath} for {current_date}")
        
        with h5py.File(filepath, 'w') as f:
            # Metadata
            meta_grp = f.create_group('metadata')
            meta_grp.attrs['save_date'] = current_date.isoformat()
            if metadata:
                for key, value in metadata.items():
                    meta_grp.attrs[key] = value
            
            # State group
            state_grp = f.create_group('state')
            
            # Save heads
            if heads:
                cell_ids = np.array(list(heads.keys()), dtype=np.int32)
                head_values = np.array(list(heads.values()), dtype=np.float64)
                state_grp.create_dataset('cell_ids', data=cell_ids)
                state_grp.create_dataset('heads', data=head_values)
            
            # Save soil water
            if soil_water:
                hru_ids = np.array(list(soil_water.keys()), dtype=np.int32)
                sw_values = np.array(list(soil_water.values()), dtype=np.float64)
                state_grp.create_dataset('hru_ids', data=hru_ids)
                state_grp.create_dataset('soil_water', data=sw_values)
            
            # Save recharge delay state
            if recharge_delay_state:
                delay_grp = state_grp.create_group('recharge_delay')
                for hru_id, state in recharge_delay_state.items():
                    hru_grp = delay_grp.create_group(str(hru_id))
                    hru_grp.attrs['R_prev'] = state.get('R_prev', 0.0)
                    hru_grp.attrs['days'] = state.get('days', 0)
            
            # Save water balance timeseries
            if self.water_balance:
                df_balance = pd.DataFrame(self.water_balance)
                ts_grp = f.create_group('timeseries')
                ts_grp.create_dataset('water_balance_dates', 
                                       data=[str(d) for d in df_balance['date']])
                ts_grp.create_dataset('water_balance_components',
                                       data=df_balance['component'].astype('S'))
                ts_grp.create_dataset('water_balance_values',
                                       data=df_balance['value_m3'].values)
            
            # Save daily fluxes
            if self.daily_fluxes:
                df_fluxes = pd.DataFrame(self.daily_fluxes)
                flux_grp = f.create_group('daily_fluxes')
                flux_grp.create_dataset('dates', 
                                         data=[str(d) for d in df_fluxes['date']])
                flux_grp.create_dataset('cell_ids', data=df_fluxes['cell_id'].values)
                flux_grp.create_dataset('flux_types',
                                         data=df_fluxes['flux_type'].astype('S'))
                flux_grp.create_dataset('values', data=df_fluxes['value_m3_day'].values)
        
        self.logger.info(f"State saved successfully to {filepath}")
    
    def load_from_hdf5(
        self,
        filepath: Path
    ) -> Dict[str, Any]:
        """
        Load coupling state from HDF5 file.
        
        Args:
            filepath: Path to HDF5 file
        
        Returns:
            Dictionary with keys: metadata, heads, soil_water, recharge_delay_state
        """
        self.logger.info(f"Loading state from {filepath}")
        
        state = {
            'metadata': {},
            'heads': {},
            'soil_water': {},
            'recharge_delay_state': {}
        }
        
        with h5py.File(filepath, 'r') as f:
            # Load metadata
            if 'metadata' in f:
                meta_grp = f['metadata']
                for key in meta_grp.attrs:
                    state['metadata'][key] = meta_grp.attrs[key]
            
            # Load state
            if 'state' in f:
                state_grp = f['state']
                
                # Load heads
                if 'cell_ids' in state_grp and 'heads' in state_grp:
                    cell_ids = state_grp['cell_ids'][:]
                    heads = state_grp['heads'][:]
                    state['heads'] = dict(zip(cell_ids, heads))
                
                # Load soil water
                if 'hru_ids' in state_grp and 'soil_water' in state_grp:
                    hru_ids = state_grp['hru_ids'][:]
                    sw_values = state_grp['soil_water'][:]
                    state['soil_water'] = dict(zip(hru_ids, sw_values))
                
                # Load recharge delay state
                if 'recharge_delay' in state_grp:
                    delay_grp = state_grp['recharge_delay']
                    for hru_id_str in delay_grp:
                        hru_id = int(hru_id_str)
                        hru_grp = delay_grp[hru_id_str]
                        state['recharge_delay_state'][hru_id] = {
                            'R_prev': hru_grp.attrs['R_prev'],
                            'days': hru_grp.attrs['days']
                        }
        
        self.logger.info(f"State loaded successfully from {filepath}")
        return state
    
    def track_water_balance(
        self,
        date: datetime,
        component: str,
        value_m3: float
    ) -> None:
        """
        Track water balance component for a timestep.
        
        Args:
            date: Simulation date
            component: Component name (e.g., 'recharge', 'et', 'channel_exchange')
            value_m3: Volume (m³)
        """
        self.water_balance.append({
            'date': date,
            'component': component,
            'value_m3': value_m3
        })
    
    def track_cell_flux(
        self,
        date: datetime,
        cell_id: int,
        flux_type: str,
        value_m3_day: float
    ) -> None:
        """
        Track flux for a specific cell and timestep.
        
        Args:
            date: Simulation date
            cell_id: MODFLOW cell ID
            flux_type: Flux type (e.g., 'recharge', 'et', 'pumping', 'river')
            value_m3_day: Daily flux rate (m³/day)
        """
        self.daily_fluxes.append({
            'date': date,
            'cell_id': cell_id,
            'flux_type': flux_type,
            'value_m3_day': value_m3_day
        })
    
    def get_water_balance_summary(self) -> pd.DataFrame:
        """
        Get summary of water balance components.
        
        Returns:
            DataFrame with columns: date, component, value_m3
        """
        return pd.DataFrame(self.water_balance)
    
    def get_daily_fluxes_summary(self) -> pd.DataFrame:
        """
        Get summary of daily fluxes by cell.
        
        Returns:
            DataFrame with columns: date, cell_id, flux_type, value_m3_day
        """
        return pd.DataFrame(self.daily_fluxes)
    
    def export_to_csv(
        self,
        water_balance_path: Optional[Path] = None,
        daily_fluxes_path: Optional[Path] = None
    ) -> None:
        """
        Export tracked data to CSV files.
        
        Args:
            water_balance_path: Output path for water balance CSV
            daily_fluxes_path: Output path for daily fluxes CSV
        """
        if water_balance_path and self.water_balance:
            df = self.get_water_balance_summary()
            df.to_csv(water_balance_path, index=False)
            self.logger.info(f"Water balance exported to {water_balance_path}")
        
        if daily_fluxes_path and self.daily_fluxes:
            df = self.get_daily_fluxes_summary()
            df.to_csv(daily_fluxes_path, index=False)
            self.logger.info(f"Daily fluxes exported to {daily_fluxes_path}")
    
    def clear_memory(self) -> None:
        """Clear in-memory tracking to free memory during long simulations."""
        self.water_balance = []
        self.daily_fluxes = []
        self.logger.debug("Cleared in-memory state tracking")
