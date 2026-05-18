"""MODFLOW 6 model interface using FloPy"""

from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
import logging
import numpy as np
from ..coupling.boundary_conditions import BoundaryConditionManager

try:
    import flopy
    import flopy.mf6 as mf6
except ImportError:
    flopy = None

logger = logging.getLogger(__name__)


class MODFLOWInterface:
    """
    Interface to MODFLOW 6 model via FloPy.
    
    Manages:
    - Loading MODFLOW 6 model
    - Daily stress period updates
    - Solver execution
    - Head extraction and output
    """
    
    def __init__(
        self,
        model_dir: Path,
        executable: Optional[str | Path] = None
    ):
        """
        Initialize MODFLOW 6 interface.
        
        Args:
            model_dir: Directory containing MODFLOW 6 model
            executable: Path to mf6 executable (auto-detected if None)
        """
        if flopy is None:
            raise ImportError("flopy required: pip install flopy")
        
        self.model_dir = Path(model_dir)
        self.executable = executable
        self.sim = None  # MODFLOW simulation object
        self.grid_info = None
        self.boundary_manager = None
        
        logger.info(f"Initialized MODFLOWInterface: {self.model_dir}")
    
    def get_grid_info(self) -> Dict[str, Any]:
        """
        Get MODFLOW grid information.
        
        Reads discretization file (*.dis) to get grid properties.
        
        Returns:
            Dictionary with grid properties (nrow, ncol, nlayer, dx, dy, etc)
        """
        grid_info = {
            'nrow': 0,
            'ncol': 0,
            'nlayers': 1,
            'dx': 500.0,
            'dy': 500.0,
        }
        
        # Try to read from discretization file
        dis_files = list(self.model_dir.glob("*.dis"))
        if dis_files:
            try:
                with open(dis_files[0], 'r') as f:
                    for line in f:
                        line = line.strip().upper()
                        if 'NROW' in line:
                            parts = line.split()
                            grid_info['nrow'] = int(parts[-1])
                        elif 'NCOL' in line:
                            parts = line.split()
                            grid_info['ncol'] = int(parts[-1])
                        elif 'NLAY' in line:
                            parts = line.split()
                            grid_info['nlayers'] = int(parts[-1])
            except Exception as e:
                logger.warning(f"Could not read grid info from DIS file: {e}")
        
        self.grid_info = grid_info
        return grid_info
    
    def initialize(
        self,
        start_date: datetime,
        end_date: datetime,
        convergence_tol: float = 1e-4,
        max_iterations: int = 10000
    ) -> None:
        """
        Load and initialize MODFLOW 6 model using FloPy.
        
        Loads the MODFLOW 6 simulation from name file (mfsim.nam),
        extracts initial heads, and sets up for daily stress periods.
        
        Args:
            start_date: Simulation start date
            end_date: Simulation end date
            convergence_tol: Solver convergence tolerance
            max_iterations: Maximum iterations per timestep
        """
        logger.info(f"Initializing MODFLOW 6 model from {self.model_dir}")
        
        try:
            # Find MODFLOW simulation name file
            nam_files = list(self.model_dir.glob("*.nam")) + list(self.model_dir.glob("mfsim.nam"))
            if not nam_files:
                raise FileNotFoundError("MODFLOW simulation name file (*.nam) not found")
            
            nam_file = nam_files[0]
            logger.info(f"Loading MODFLOW simulation from {nam_file}")
            
            # Load simulation using FloPy
            self.sim = flopy.mf6.MFSimulation.load(
                sim_name=nam_file.stem,
                sim_ws=str(self.model_dir),
                verbosity_level=0
            )
            
            # Get model and grid info
            self.model = self.sim.get_model()
            self.get_grid_info()
            self.boundary_manager = BoundaryConditionManager(self.model, self.grid_info)
            
            # Extract initial heads from IC package
            ic = self.model.ic
            if ic is not None:
                initial_heads = ic.strt.get_data()
                logger.info(f"Read initial heads from IC package: shape={initial_heads.shape}")
            
            self.start_date = start_date
            self.end_date = end_date
            self.convergence_tol = convergence_tol
            self.max_iterations = max_iterations
            self.total_days = (end_date - start_date).days
            
            logger.info(f"MODFLOW 6 model loaded successfully ({self.total_days} day simulation)")
            
        except Exception as e:
            logger.error(f"Failed to initialize MODFLOW 6: {e}")
            raise
    
    def get_initial_heads(self) -> Dict[int, float]:
        """
        Get initial groundwater heads from MODFLOW IC package.
        
        Returns:
            Dictionary mapping cell ID to initial head (m)
        """
        heads_dict = {}
        try:
            if self.model and hasattr(self.model, 'ic') and self.model.ic:
                strt = self.model.ic.strt.get_data()
                # Convert 3D array (nlay, nrow, ncol) to cell ID
                nlay, nrow, ncol = strt.shape
                cell_id = 0
                for k in range(nlay):
                    for i in range(nrow):
                        for j in range(ncol):
                            heads_dict[cell_id] = float(strt[k, i, j])
                            cell_id += 1
                logger.info(f"Extracted {len(heads_dict)} initial heads")
        except Exception as e:
            logger.warning(f"Could not extract initial heads: {e}")
        
        return heads_dict
    
    def advance_one_day(
        self,
        recharge_cells: Dict[int, float],
        et_cells: Dict[int, float],
        pumping_cells: Dict[int, float],
        channel_stages: Dict[int, float],
        current_date: datetime
    ) -> Dict[str, Any]:
        """
        Advance MODFLOW 6 one day with updated stresses.
        
        Updates stress period packages with daily values from SWAT+:
        - RCH package: soil percolation recharge
        - EVT package: unsatisfied ET extraction
        - WEL package: irrigation pumping rates
        - RIV package: channel stage and exchange
        
        Args:
            recharge_cells: Cell ID -> recharge rate (m³/day)
            et_cells: Cell ID -> max ET removal rate (m³/day)
            pumping_cells: Cell ID -> pumping rate (m³/day)
            channel_stages: Cell ID -> river/stream stage (m)
            current_date: Current simulation date
        
        Returns:
            Dictionary with results:
            - heads: Cell ID -> groundwater head (m)
            - channel_exchange: Cell ID -> RIV exchange rate (m³/day)
            - drain_exchange: Cell ID -> DRN outflow (m³/day)
            - budget: Global water budget
        """
        try:
            # Update stress period (stress period 1 = daily)
            self._update_stress_period(
                recharge_cells=recharge_cells,
                et_cells=et_cells,
                pumping_cells=pumping_cells,
                channel_stages=channel_stages
            )
            
            # Run MODFLOW solver
            success = self._run_solver()
            
            if not success:
                logger.error(f"MODFLOW solver failed on {current_date}")
                return {}
            
            # Extract results from solution
            results = {
                'heads': self._extract_heads(),
                'channel_exchange': self._extract_package_exchange('RIV'),
                'drain_exchange': self._extract_package_exchange('DRN'),
                'budget': self._get_water_budget(),
            }
            
            return results
            
        except Exception as e:
            logger.error(f"Error in advance_one_day: {e}")
            return {}
    
    def _update_stress_period(
        self,
        recharge_cells: Dict[int, float],
        et_cells: Dict[int, float],
        pumping_cells: Dict[int, float],
        channel_stages: Dict[int, float]
    ) -> None:
        """
        Update boundary conditions for current stress period.
        
        Updates:
        - RCH: recharge package (m³/day by cell)
        - EVT: evapotranspiration package (m³/day by cell)
        - WEL: well package (m³/day by cell, negative = extraction)
        - RIV: river/stream package (stage and conductance)
        """
        if not self.model:
            return

        try:
            if self.boundary_manager is None:
                self.boundary_manager = BoundaryConditionManager(self.model, self.grid_info)

            self.boundary_manager.update_recharge_package(recharge_cells, stress_period=0)
            self.boundary_manager.update_et_package(et_cells, stress_period=0)
            self.boundary_manager.update_well_package(pumping_cells, stress_period=0)
            self.boundary_manager.update_river_package(
                {
                    cell_id: {"stage": stage}
                    for cell_id, stage in channel_stages.items()
                },
                stress_period=0,
            )

        except Exception as e:
            logger.warning(f"Error updating stress period packages: {e}")
    
    def _update_package_celldata(self, package_name: str, cell_values: Dict[int, float], field_name: str) -> None:
        """Update a MODFLOW package cell-by-cell data (RCH, EVT, etc.)"""
        pkg = getattr(self.model, package_name, None)
        if not pkg:
            return
        
        try:
            # For each stress period, update the data
            spd = pkg.spd
            if spd:
                for isp in range(len(spd)):
                    spd_data = {}
                    for cell_id, value in cell_values.items():
                        # Convert cell ID to (k, i, j) indices
                        k, i, j = self._cell_id_to_indices(cell_id)
                        spd_data[(k, i, j)] = value
                    if spd_data:
                        spd[isp][field_name] = spd_data
        except Exception as e:
            logger.debug(f"Could not update {package_name}: {e}")
    
    def _update_well_package(self, pumping_cells: Dict[int, float]) -> None:
        """Update WEL package with pumping rates"""
        try:
            wel = self.model.wel
            wel_list = []
            
            for cell_id, rate in pumping_cells.items():
                k, i, j = self._cell_id_to_indices(cell_id)
                wel_list.append((k, i, j, rate))  # rate negative for extraction
            
            if wel_list:
                wel_array = np.array(wel_list, dtype=[('k', int), ('i', int), ('j', int), ('q', float)])
                for isp in range(self.sim.tdis.nper.get_data()):
                    wel.stress_period_data[isp] = wel_array
        except Exception as e:
            logger.debug(f"Could not update WEL package: {e}")
    
    def _update_river_package(self, channel_stages: Dict[int, float]) -> None:
        """Update RIV package with channel stage"""
        try:
            riv = self.model.riv
            riv_list = []
            
            for cell_id, stage in channel_stages.items():
                k, i, j = self._cell_id_to_indices(cell_id)
                # RIV format: (k, i, j, stage, conductance, bottom_elevation)
                # Extract conductance and elevation from existing data if available
                riv_list.append((k, i, j, stage, 100.0, stage - 1.0))  # Defaults for now
            
            if riv_list:
                riv_array = np.array(riv_list, dtype=[('k', int), ('i', int), ('j', int), ('stage', float), ('cond', float), ('rbot', float)])
                for isp in range(self.sim.tdis.nper.get_data()):
                    riv.stress_period_data[isp] = riv_array
        except Exception as e:
            logger.debug(f"Could not update RIV package: {e}")
    
    def _cell_id_to_indices(self, cell_id: int) -> tuple:
        """Convert 1D cell ID to 3D (k, i, j) indices for MODFLOW grid"""
        if not self.grid_info:
            self.get_grid_info()
        
        nrow = self.grid_info.get('nrow', 1)
        ncol = self.grid_info.get('ncol', 1)
        
        k = cell_id // (nrow * ncol)
        remainder = cell_id % (nrow * ncol)
        i = remainder // ncol
        j = remainder % ncol
        
        return (k, i, j)
    
    def _run_solver(self) -> bool:
        """Run MODFLOW 6 solver"""
        try:
            if not self.sim:
                return False
            
            # Run simulation
            self.sim.run_simulation(silent=True)
            logger.debug("MODFLOW solver completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"MODFLOW solver error: {e}")
            return False
    
    def _extract_heads(self) -> Dict[int, float]:
        """
        Extract groundwater heads from MODFLOW solution.
        
        Reads from HEAD (*.hds) output file using FloPy.
        """
        heads_dict = {}
        try:
            if not self.model:
                return heads_dict
            
            # Read heads from output file
            head_file = list(self.model_dir.glob("*.hds"))
            if head_file:
                hds = flopy.utils.HeadFile(str(head_file[0]))
                heads_array = hds.get_data(totim=hds.get_times()[-1])  # Last time step
                
                # Convert 3D array to cell ID dictionary
                nlay, nrow, ncol = heads_array.shape
                cell_id = 0
                for k in range(nlay):
                    for i in range(nrow):
                        for j in range(ncol):
                            h = heads_array[k, i, j]
                            if not np.isnan(h):
                                heads_dict[cell_id] = float(h)
                            cell_id += 1
                
                logger.debug(f"Extracted {len(heads_dict)} heads from solution")
        except Exception as e:
            logger.debug(f"Could not extract heads: {e}")
        
        return heads_dict
    
    def _extract_package_exchange(self, package_name: str) -> Dict[int, float]:
        """
        Extract exchange rates from MODFLOW 6 packages (RIV, DRN, GHB, etc).
        
        Reads from cell-by-cell budget file (*.cbc) produced by MODFLOW 6.
        MODFLOW 6 CBC records use structured numpy arrays with fields like
        'node' and 'q' for list-based packages (RIV, DRN, GHB, WEL), or
        full 3D arrays for array-based packages (RCH, EVT).
        
        Args:
            package_name: Budget text identifier (e.g. 'RIV', 'DRN', 'WEL')
        
        Returns:
            Cell ID -> exchange rate (m³/day). Positive = into aquifer.
        """
        exchange_dict = {}
        try:
            cbc_files = list(self.model_dir.glob("*.cbc"))
            if not cbc_files:
                return exchange_dict
            
            cbc = flopy.utils.CellBudgetFile(str(cbc_files[0]))
            times = cbc.get_times()
            if not times:
                return exchange_dict
            
            # Get unique record names to find matching package
            record_names = [name.strip().decode() if isinstance(name, bytes)
                           else name.strip()
                           for name in cbc.get_unique_record_names()]
            
            # Find matching record (MODFLOW 6 uses names like 'RIV', 'DRN', etc.)
            matched_name = None
            for rname in record_names:
                if package_name.upper() in rname.upper():
                    matched_name = rname
                    break
            
            if matched_name is None:
                logger.debug(f"Package '{package_name}' not found in CBC records: {record_names}")
                return exchange_dict
            
            # Extract data for the last timestep
            data_list = cbc.get_data(text=matched_name, totim=times[-1])
            
            if data_list is None or len(data_list) == 0:
                return exchange_dict
            
            for data in data_list:
                if isinstance(data, np.recarray):
                    # List-based package (RIV, DRN, GHB, WEL, etc.)
                    # MODFLOW 6 records have 'node' and 'q' fields
                    if 'node' in data.dtype.names and 'q' in data.dtype.names:
                        for rec in data:
                            # node is 1-based in MODFLOW 6
                            cell_id = int(rec['node']) - 1
                            q = float(rec['q'])
                            exchange_dict[cell_id] = exchange_dict.get(cell_id, 0.0) + q
                    elif 'node2' in data.dtype.names and 'q' in data.dtype.names:
                        # Flow-ja-face or advanced package format
                        for rec in data:
                            cell_id = int(rec['node2']) - 1
                            q = float(rec['q'])
                            exchange_dict[cell_id] = exchange_dict.get(cell_id, 0.0) + q
                elif isinstance(data, np.ndarray):
                    # Array-based package (RCH, EVT) — full 3D array
                    if data.ndim == 3:
                        nlay, nrow, ncol = data.shape
                        cell_id = 0
                        for k in range(nlay):
                            for i in range(nrow):
                                for j in range(ncol):
                                    val = float(data[k, i, j])
                                    if val != 0.0 and not np.isnan(val):
                                        exchange_dict[cell_id] = val
                                    cell_id += 1
                    elif data.ndim == 2:
                        # Single-layer array
                        nrow, ncol = data.shape
                        cell_id = 0
                        for i in range(nrow):
                            for j in range(ncol):
                                val = float(data[i, j])
                                if val != 0.0 and not np.isnan(val):
                                    exchange_dict[cell_id] = val
                                cell_id += 1
            
            logger.debug(f"Extracted {package_name} exchanges: {len(exchange_dict)} cells")
        except Exception as e:
            logger.debug(f"Could not extract {package_name} exchanges: {e}")
        
        return exchange_dict
    
    def _get_water_budget(self) -> Dict[str, float]:
        """
        Get global water budget from MODFLOW solver.
        
        Reads summary budget information.
        """
        budget = {
            'recharge_in': 0.0,
            'et_out': 0.0,
            'pumping_out': 0.0,
            'storage_change': 0.0,
            'channel_exchange': 0.0,
        }
        
        try:
            list_file = list(self.model_dir.glob("*.lst"))
            if list_file:
                # Parse list file for budget information
                with open(list_file[0], 'r') as f:
                    for line in f:
                        if 'RECHARGE' in line.upper():
                            parts = line.split()
                            if len(parts) > 2:
                                try:
                                    budget['recharge_in'] = float(parts[-1])
                                except ValueError:
                                    pass
        except Exception as e:
            logger.debug(f"Could not extract budget: {e}")
        
        return budget
