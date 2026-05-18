"""SWAT+ model interface"""

from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
import subprocess
import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class SWATPlusInterface:
    """
    Interface to SWAT+ model executable.
    
    Manages:
    - Model initialization
    - Daily timestep execution
    - Input/output file handling
    - Variable extraction from SWAT+ objects
    """
    
    def __init__(
        self,
        model_dir: Path,
        executable: Optional[str | Path] = None
    ):
        """
        Initialize SWAT+ interface.
        
        Args:
            model_dir: Directory containing SWAT+ model files
            executable: Path to swatplus executable (auto-detected if None)
        """
        self.model_dir = Path(model_dir)
        self.executable = executable or self._find_executable()
        
        # QSWAT+/SWAT+ Editor usually uses a flat TxtInOut directory. Keep
        # support for the older input/output split used by early scaffolding.
        if (self.model_dir / "file.cio").exists():
            self.input_dir = self.model_dir
            self.output_dir = self.model_dir
        else:
            self.input_dir = self.model_dir / "input"
            self.output_dir = self.model_dir / "output"
        
        self.initialized = False
        logger.info(f"Initialized SWATPlusInterface: {self.model_dir}")
        logger.info(f"  SWAT+ executable: {self.executable}")
    
    def _find_executable(self) -> Path:
        """Find SWAT+ executable in common locations"""
        # Check model directory
        for exe_name in ["swatplus", "swat+", "swat"]:
            exe_path = self.model_dir / exe_name
            if exe_path.exists():
                return exe_path
        
        # Check PATH
        result = subprocess.run(
            ["where" if subprocess.os.name == "nt" else "which", "swatplus"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return Path(result.stdout.strip().split('\n')[0])
        
        raise FileNotFoundError("SWAT+ executable not found")
    
    def get_object_info(self) -> Dict[str, Any]:
        """
        Get information about SWAT+ objects (HRUs, channels, etc).
        
        Reads from SWAT+ input files to determine object counts and properties.
        
        Returns:
            Dictionary with object counts and properties
        """
        info = {
            'n_hrus': self._count_hrus(),
            'n_channels': self._count_channels(),
            'n_reservoirs': self._count_reservoirs(),
            'n_canals': self._count_canals(),
            'n_drains': self._count_drains(),
            'hru_properties': self._read_hru_properties(),
            'channel_properties': self._read_channel_properties(),
        }
        return info
    
    def _count_hrus(self) -> int:
        """Count HRUs from SWAT+ input files"""
        hru_file = self._find_input_file("hru-data.hru", "hru_data.hru")
        if hru_file.exists():
            return len(self._read_swat_input_table(hru_file))
        return 0
    
    def _count_channels(self) -> int:
        """Count channels from SWAT+ input files"""
        channel_file = self._find_input_file("channel-lte.cha", "channel.cha", "channel_reach.chm")
        if channel_file.exists():
            return len(self._read_swat_input_table(channel_file))
        return 0
    
    def _count_reservoirs(self) -> int:
        """Count reservoirs from SWAT+ input files"""
        res_file = self._find_input_file("reservoir.res")
        if res_file.exists():
            return len(self._read_swat_input_table(res_file))
        return 0
    
    def _count_canals(self) -> int:
        """Count canals from SWAT+ input files"""
        return 0
    
    def _count_drains(self) -> int:
        """Count tile drains from SWAT+ input files"""
        return 0
    
    def _read_hru_properties(self) -> Dict[int, Dict[str, Any]]:
        """Read HRU properties from SWAT+ input"""
        hru_props = {}

        hru_con_file = self._find_input_file("hru.con", "hru_con.con")
        if hru_con_file.exists():
            try:
                df = self._read_swat_input_table(hru_con_file)
                for idx, row in df.iterrows():
                    hru_id = int(row.get('hru', row.get('id', idx + 1)))
                    area_ha = float(row.get('area', 0.0))
                    hru_props[hru_id] = {
                        'area_ha': area_ha,
                        'area_km2': area_ha * 0.01,
                        'gis_id': int(row.get('gis_id', hru_id)),
                        'lat': float(row.get('lat', 0.0)),
                        'lon': float(row.get('lon', 0.0)),
                        'elev_m': float(row.get('elev', 0.0)),
                    }
            except Exception as e:
                logger.warning(f"Could not read HRU connection properties: {e}")

        hru_data_file = self._find_input_file("hru-data.hru", "hru_data.hru")
        if hru_data_file.exists():
            try:
                df = self._read_swat_input_table(hru_data_file)
                for idx, row in df.iterrows():
                    hru_id = int(row.get('id', idx + 1))
                    props = hru_props.setdefault(hru_id, {})
                    props.update({
                        'soil_id': int(row.get('soil', 0)),
                        'subbasin': int(row.get('subbasin', 0)),
                        'topography': row.get('topo', ''),
                        'hydrology': row.get('hydro', ''),
                        'landuse_mgt': row.get('lu_mgt', ''),
                    })
            except Exception as e:
                logger.warning(f"Could not read HRU properties: {e}")
        return hru_props
    
    def _read_channel_properties(self) -> Dict[int, Dict[str, Any]]:
        """Read channel properties from SWAT+ input"""
        channel_props = {}
        channel_file = self._find_input_file("channel-lte.cha", "channel.cha", "channel_reach.chm")
        if channel_file.exists():
            try:
                df = self._read_swat_input_table(channel_file)
                for idx, row in df.iterrows():
                    channel_id = int(row.get('id', idx + 1))
                    channel_props[channel_id] = {
                        'reach_id': int(row.get('reach', idx)),
                        'length_m': float(row.get('len1', 0)),
                        'width_m': float(row.get('wid1', 0)),
                        'slope': float(row.get('slope', 0)),
                        'manning_n': float(row.get('n', 0.04)),
                    }
            except Exception as e:
                logger.warning(f"Could not read channel properties: {e}")
        return channel_props

    def _find_input_file(self, *names: str) -> Path:
        """Find the first matching SWAT+ input file name."""
        for name in names:
            path = self.input_dir / name
            if path.exists():
                return path
        return self.input_dir / names[0]

    def _read_swat_input_table(self, filepath: Path) -> pd.DataFrame:
        """Read a fixed-width-looking SWAT+ input table with a title row."""
        df = pd.read_csv(filepath, sep=r'\s+', skiprows=1, engine='python')
        df.columns = [str(col).strip() for col in df.columns]
        return df

    def _read_swat_output_table(self, filepath: Path) -> pd.DataFrame:
        """
        Read SWAT+ text/CSV output.

        Output files have a title line, a comma-delimited header line, a units
        line, then data. Header names often contain padding spaces.
        """
        if filepath.suffix.lower() == ".csv":
            df = pd.read_csv(filepath, skiprows=[0, 2], skipinitialspace=True)
        else:
            df = pd.read_csv(filepath, sep=r'\s+', skiprows=[0, 2], engine='python')

        df.columns = [str(col).strip() for col in df.columns]
        for col in df.select_dtypes(include="object").columns:
            df[col] = df[col].astype(str).str.strip()
        return df
    
    def initialize(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> None:
        """
        Initialize SWAT+ model for simulation.
        
        Sets up date tracking, verifies the executable, and ensures
        the output directory exists.
        
        Args:
            start_date: Simulation start date
            end_date: Simulation end date
        """
        logger.info(f"Initializing SWAT+ for {start_date} to {end_date}")
        self.start_date = start_date
        self.end_date = end_date
        self.current_day_index = 0
        
        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Cache output DataFrames to avoid re-reading every timestep
        self._output_cache: Dict[str, pd.DataFrame] = {}
        self._cache_loaded = False
        
        self.initialized = True
    
    def advance_one_day(self, current_date: datetime) -> Dict[str, Any]:
        """
        Advance SWAT+ simulation one day and extract outputs.
        
        Runs the SWAT+ executable (the full simulation runs once, then
        daily outputs are extracted from the output files by date index).
        On the first call, the executable is invoked and the output files
        are read into memory.  Subsequent calls simply index into the
        cached output for the given date.
        
        Args:
            current_date: Current simulation date
        
        Returns:
            Dictionary with extracted daily outputs:
            - recharge: dict of HRU -> recharge (mm/day)
            - unsatisfied_et: dict of HRU -> remaining ET demand (mm/day)
            - channel_stage: dict of channel -> stage (m)
            - irrigation_demand: dict of HRU -> demand (m³/day)
            - soil_water: dict of HRU -> soil water content (mm)
        """
        if not self.initialized:
            raise RuntimeError("Must call initialize() first")
        
        # Run SWAT+ executable on the first timestep
        if not self._cache_loaded:
            self._run_swatplus_executable()
            self._load_output_cache()
            self._cache_loaded = True
        
        # Calculate 0-based day index from start
        day_index = (current_date - self.start_date).days
        
        # Extract outputs for this specific day
        outputs = {
            'recharge': self._extract_recharge(current_date, day_index),
            'unsatisfied_et': self._extract_unsatisfied_et(current_date, day_index),
            'channel_stage': self._extract_channel_stage(current_date, day_index),
            'irrigation_demand': self._extract_irrigation_demand(current_date, day_index),
            'soil_water': self._extract_soil_water(current_date, day_index),
        }
        
        self.current_day_index = day_index + 1
        return outputs
    
    def _run_swatplus_executable(self) -> None:
        """
        Run the SWAT+ executable in the model directory.
        
        SWAT+ is invoked from its model directory; it reads input files
        and writes daily output files.  The process blocks until the full
        simulation period completes.
        """
        logger.info(f"Running SWAT+ executable: {self.executable}")
        try:
            result = subprocess.run(
                [str(self.executable)],
                cwd=str(self.model_dir),
                capture_output=True,
                text=True,
                timeout=3600  # 1-hour timeout
            )
            if result.returncode != 0:
                logger.error(f"SWAT+ execution failed (rc={result.returncode})")
                logger.error(f"  stdout: {result.stdout[-500:]}")
                logger.error(f"  stderr: {result.stderr[-500:]}")
                raise RuntimeError(f"SWAT+ execution failed with code {result.returncode}")
            logger.info("SWAT+ execution completed successfully")
        except FileNotFoundError:
            logger.warning(
                f"SWAT+ executable not found at {self.executable}; "
                f"reading pre-existing output files instead"
            )
        except subprocess.TimeoutExpired:
            logger.error("SWAT+ execution timed out (>3600s)")
            raise
    
    def _load_output_cache(self) -> None:
        """
        Load all SWAT+ output files into memory for fast daily look-up.
        
        Caches HRU landscape (hru_ls_*) and channel (ch_ls_*) output.
        """
        current_run_mtime = self._current_run_mtime()

        # Cache HRU output. hru_wb carries water-balance fields such as perc,
        # et, pet, sw_final; hru_ls carries sediment/nutrient landscape fields.
        hru_files = (
            sorted(self.output_dir.glob("hru_wb_*"))
            + sorted(self.output_dir.glob("hru_ls_*"))
            + sorted(self.output_dir.glob("hru_pw_*"))
        )
        for hru_file in hru_files:
            if not self._is_current_output_file(hru_file, current_run_mtime):
                continue
            try:
                df = self._read_swat_output_table(hru_file)
                self._output_cache[f'hru_{hru_file.name}'] = df
                logger.debug(f"Cached {hru_file.name}: {len(df)} rows")
            except Exception as e:
                logger.debug(f"Could not cache {hru_file}: {e}")
        
        # Cache channel output
        ch_files = sorted(self.output_dir.glob("channel_*")) + sorted(self.output_dir.glob("ch_*"))
        for ch_file in ch_files:
            if not self._is_current_output_file(ch_file, current_run_mtime):
                continue
            try:
                df = self._read_swat_output_table(ch_file)
                self._output_cache[f'ch_{ch_file.name}'] = df
                logger.debug(f"Cached {ch_file.name}: {len(df)} rows")
            except Exception as e:
                logger.debug(f"Could not cache {ch_file}: {e}")

    def _current_run_mtime(self) -> Optional[float]:
        """Return the timestamp for the current SWAT+ run marker, if present."""
        markers = [
            path.stat().st_mtime
            for path in (self.output_dir / "print.prt", self.output_dir / "simulation.out")
            if path.exists()
        ]
        return min(markers) if markers else None

    def _is_current_output_file(self, path: Path, run_mtime: Optional[float]) -> bool:
        """Avoid mixing stale SWAT+ output files from previous runs."""
        if run_mtime is None:
            return True
        return path.stat().st_mtime >= run_mtime - 60.0
    
    def _filter_by_day(self, df: pd.DataFrame, day_index: int) -> pd.DataFrame:
        """
        Filter a SWAT+ output DataFrame to a single day.
        
        SWAT+ daily output files have columns like 'jday', 'yr', 'mon',
        'day' or a combined date column.  This method tries several
        strategies to isolate the rows for a given simulation day.
        
        Args:
            df: Output DataFrame
            day_index: 0-based day index from simulation start
        
        Returns:
            Filtered DataFrame containing rows for the target day.
        """
        # Strategy 1: If there's a 'jday' column, each unique jday is a day
        if 'jday' in df.columns and 'yr' in df.columns:
            # Build a unique-day list and select by position
            day_groups = df.groupby(['yr', 'jday'])
            day_keys = list(day_groups.groups.keys())
            if day_index < len(day_keys):
                key = day_keys[day_index]
                return day_groups.get_group(key)
        
        # Strategy 2: Look for date or day column
        for date_col in ['date', 'Date', 'DATE']:
            if date_col in df.columns:
                unique_dates = df[date_col].unique()
                if day_index < len(unique_dates):
                    return df[df[date_col] == unique_dates[day_index]]
        
        # Strategy 3: Assume rows repeat per object per day
        # Count unique object IDs to determine rows-per-day
        for id_col in ['gis_id', 'unit', 'name', 'id']:
            if id_col in df.columns:
                n_objects = df[id_col].nunique()
                if n_objects > 0:
                    start_row = day_index * n_objects
                    end_row = start_row + n_objects
                    if end_row <= len(df):
                        return df.iloc[start_row:end_row]
                    break
        
        # Fallback: return entire DataFrame (original behaviour)
        logger.debug(f"Could not filter by day index {day_index}; returning full frame")
        return df
    
    def _extract_recharge(self, current_date: datetime, day_index: int) -> Dict[int, float]:
        """
        Extract daily deep percolation (recharge) from SWAT+ HRU output.
        
        Reads the cached hru_ls_* output and filters to the target day.
        Extracts the 'perc' (percolation) column in mm/day.
        """
        recharge = {}
        
        for cache_key, df in self._output_cache.items():
            if not cache_key.startswith('hru_') or not self._is_daily_cache_key(cache_key):
                continue
            try:
                day_df = self._filter_by_day(df, day_index)
                
                # Find percolation column
                perc_col = None
                for col in day_df.columns:
                    if 'perc' in col.lower():
                        perc_col = col
                        break
                if perc_col is None:
                    continue
                
                # Find HRU ID column
                id_col = self._find_id_column(day_df)
                
                for _, row in day_df.iterrows():
                    hru_id = int(row[id_col]) if id_col else int(row.name) + 1
                    perc_val = float(row[perc_col])
                    if not np.isnan(perc_val) and perc_val >= 0:
                        recharge[hru_id] = perc_val
            except Exception as e:
                logger.debug(f"Could not read recharge from {cache_key}: {e}")
        
        return recharge
    
    def _extract_unsatisfied_et(self, current_date: datetime, day_index: int) -> Dict[int, float]:
        """Extract unsatisfied ET (PET − AET) from HRU output for a single day."""
        unsatisfied_et = {}
        
        for cache_key, df in self._output_cache.items():
            if not cache_key.startswith('hru_') or not self._is_daily_cache_key(cache_key):
                continue
            try:
                day_df = self._filter_by_day(df, day_index)
                
                pet_col = aet_col = None
                for col in day_df.columns:
                    if 'pet' in col.lower():
                        pet_col = col
                    if col.lower() in ('et', 'aet', 'eta'):
                        aet_col = col
                
                if pet_col is None or aet_col is None:
                    continue
                
                id_col = self._find_id_column(day_df)
                
                for _, row in day_df.iterrows():
                    hru_id = int(row[id_col]) if id_col else int(row.name) + 1
                    pet = float(row[pet_col])
                    aet = float(row[aet_col])
                    if not (np.isnan(pet) or np.isnan(aet)):
                        unsatisfied = max(0.0, pet - aet)
                        if unsatisfied > 0:
                            unsatisfied_et[hru_id] = unsatisfied
            except Exception as e:
                logger.debug(f"Could not extract ET from {cache_key}: {e}")
        
        return unsatisfied_et
    
    def _extract_channel_stage(self, current_date: datetime, day_index: int) -> Dict[int, float]:
        """Extract channel stage (depth) from SWAT+ channel output for a single day."""
        channel_stage = {}
        
        for cache_key, df in self._output_cache.items():
            if not cache_key.startswith('ch_') or not self._is_daily_cache_key(cache_key):
                continue
            try:
                day_df = self._filter_by_day(df, day_index)
                
                depth_col = None
                for col in day_df.columns:
                    if 'depth' in col.lower():
                        depth_col = col
                        break
                if depth_col is None:
                    continue
                
                id_col = self._find_id_column(day_df)
                
                for _, row in day_df.iterrows():
                    ch_id = int(row[id_col]) if id_col else int(row.name) + 1
                    depth = float(row[depth_col])
                    if not np.isnan(depth) and depth > 0:
                        channel_stage[ch_id] = depth
            except Exception as e:
                logger.debug(f"Could not extract channel stage from {cache_key}: {e}")
        
        return channel_stage
    
    def _extract_irrigation_demand(self, current_date: datetime, day_index: int) -> Dict[int, float]:
        """Extract irrigation demand from SWAT+ water allocation output."""
        irrigation_demand = {}
        # Would read from water allocation module output (wallo_*)
        # Placeholder — requires SWAT+ water-allocation output files
        return irrigation_demand
    
    def _extract_soil_water(self, current_date: datetime, day_index: int) -> Dict[int, float]:
        """Extract soil water content from SWAT+ HRU output for a single day."""
        soil_water = {}
        
        for cache_key, df in self._output_cache.items():
            if not cache_key.startswith('hru_') or not self._is_daily_cache_key(cache_key):
                continue
            try:
                day_df = self._filter_by_day(df, day_index)
                
                sw_col = None
                for col in day_df.columns:
                    if col.lower() in ('sw', 'sw_ave', 'sw_final'):
                        sw_col = col
                        break
                if sw_col is None:
                    continue
                
                id_col = self._find_id_column(day_df)
                
                for _, row in day_df.iterrows():
                    hru_id = int(row[id_col]) if id_col else int(row.name) + 1
                    sw = float(row[sw_col])
                    if not np.isnan(sw):
                        soil_water[hru_id] = sw
            except Exception as e:
                logger.debug(f"Could not extract soil water from {cache_key}: {e}")
        
        return soil_water
    
    def _find_id_column(self, df: pd.DataFrame) -> Optional[str]:
        """Find the object ID column in a SWAT+ output DataFrame."""
        for col in ['gis_id', 'unit', 'id', 'name']:
            if col in df.columns:
                return col
        return None

    def _is_daily_cache_key(self, cache_key: str) -> bool:
        """Return True for SWAT+ daily output cache entries."""
        return "_day." in cache_key
    
    def update_groundwater_state(
        self,
        baseflow: Dict[int, float],
        soil_saturation: Dict[int, float],
        drain_flow: Dict[int, float]
    ) -> None:
        """
        Update SWAT+ with groundwater feedback.
        
        Updates SWAT+ state with:
        - Baseflow contribution to channels
        - Soil water from rising water table
        - Tile drain discharge to channels
        
        Args:
            baseflow: Channel -> baseflow (m³/day)
            soil_saturation: HRU -> soil saturation (mm)
            drain_flow: Drain -> channel flow (m³/day)
        """
        # In production implementation, would update SWAT+ internal state arrays
        # This would require direct access to SWAT+ model memory or via intermediate files
        logger.debug(f"Updated SWAT+ groundwater state with {len(baseflow)} channels baseflow")
        logger.debug(f"Updated soil saturation for {len(soil_saturation)} HRUs")
        logger.debug(f"Updated drainage from {len(drain_flow)} drains")
