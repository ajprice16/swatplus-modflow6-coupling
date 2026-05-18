"""
Water budget tracking utilities for SWAT+ - MODFLOW 6 coupling.

Computes and validates mass balance across the coupled system,
tracking all flux pathways between models.
"""

from typing import Dict, List
from dataclasses import dataclass
from datetime import datetime
import numpy as np
import logging

logger = logging.getLogger(__name__)


@dataclass
class WaterBudget:
    """
    Daily water budget for coupled SWAT+ - MODFLOW 6 system.
    
    Tracks all major flux pathways:
    - SWAT+ → MODFLOW: recharge, unsatisfied ET
    - MODFLOW → SWAT+: channel exchange, saturation feedback
    - Internal fluxes: pumping, drains
    
    Units: m³/day (volumetric rates)
    Sign convention:
        Positive = addition to aquifer
        Negative = extraction from aquifer
    """
    date: datetime
    
    # SWAT+ → MODFLOW fluxes
    recharge_m3: float = 0.0          # Deep percolation → aquifer recharge
    unsatisfied_et_m3: float = 0.0    # Groundwater ET extraction (negative)
    channel_recharge_m3: float = 0.0  # Channel losing reaches
    
    # MODFLOW → SWAT+ fluxes
    channel_discharge_m3: float = 0.0  # Aquifer → channel (baseflow)
    saturation_feedback_m3: float = 0.0  # Water table rise → soil water
    
    # Internal MODFLOW fluxes
    pumping_m3: float = 0.0           # Well extraction (negative)
    drain_outflow_m3: float = 0.0     # Tile drain discharge
    
    # Storage changes
    modflow_storage_change_m3: float = 0.0  # Aquifer head change
    swat_soil_storage_change_m3: float = 0.0  # Soil water change
    
    def compute_net_aquifer_flux(self) -> float:
        """
        Compute net flux to aquifer.
        
        Returns:
            Net aquifer flux (m³/day), positive = recharge
        """
        return (
            self.recharge_m3 
            + self.unsatisfied_et_m3  # negative
            + self.channel_recharge_m3
            - self.channel_discharge_m3
            + self.pumping_m3  # negative
            - self.drain_outflow_m3
        )
    
    def compute_mass_balance_error(self) -> float:
        """
        Compute mass balance error as residual.
        
        Error = (Inflows - Outflows) - ΔStorage
        
        Returns:
            Mass balance error (m³/day)
        """
        net_flux = self.compute_net_aquifer_flux()
        error = net_flux - self.modflow_storage_change_m3
        return error
    
    def get_mass_balance_percent_error(self, threshold_m3: float = 1.0) -> float:
        """
        Compute mass balance error as percentage of total fluxes.
        
        Args:
            threshold_m3: Minimum flux magnitude threshold
        
        Returns:
            Percent error (0-100)
        """
        total_flux = abs(
            self.recharge_m3 
            + abs(self.unsatisfied_et_m3)
            + abs(self.channel_discharge_m3)
            + abs(self.pumping_m3)
        )
        
        if total_flux < threshold_m3:
            return 0.0
        
        error = abs(self.compute_mass_balance_error())
        return (error / total_flux) * 100.0
    
    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary for DataFrame export."""
        return {
            'date': self.date,
            'recharge_m3': self.recharge_m3,
            'unsatisfied_et_m3': self.unsatisfied_et_m3,
            'channel_recharge_m3': self.channel_recharge_m3,
            'channel_discharge_m3': self.channel_discharge_m3,
            'saturation_feedback_m3': self.saturation_feedback_m3,
            'pumping_m3': self.pumping_m3,
            'drain_outflow_m3': self.drain_outflow_m3,
            'modflow_storage_change_m3': self.modflow_storage_change_m3,
            'swat_soil_storage_change_m3': self.swat_soil_storage_change_m3,
            'net_aquifer_flux_m3': self.compute_net_aquifer_flux(),
            'mass_balance_error_m3': self.compute_mass_balance_error(),
            'mass_balance_percent_error': self.get_mass_balance_percent_error()
        }


class WaterBudgetTracker:
    """
    Tracks and validates water budget for coupled simulation.
    
    Accumulates daily budgets and provides summary statistics.
    """
    
    def __init__(self):
        self.daily_budgets: List[WaterBudget] = []
        self.logger = logging.getLogger(__name__)
    
    def add_daily_budget(self, budget: WaterBudget) -> None:
        """
        Add daily water budget.
        
        Args:
            budget: Daily water budget
        """
        self.daily_budgets.append(budget)
        
        # Check for excessive error
        percent_error = budget.get_mass_balance_percent_error()
        if percent_error > 5.0:
            self.logger.warning(
                f"Large mass balance error on {budget.date}: {percent_error:.2f}% "
                f"({budget.compute_mass_balance_error():.2f} m³/day)"
            )
    
    def get_cumulative_budget(self) -> Dict[str, float]:
        """
        Get cumulative water budget for entire simulation.
        
        Returns:
            Dictionary with cumulative values (m³)
        """
        if not self.daily_budgets:
            return {}
        
        cumulative = {
            'recharge_m3': sum(b.recharge_m3 for b in self.daily_budgets),
            'unsatisfied_et_m3': sum(b.unsatisfied_et_m3 for b in self.daily_budgets),
            'channel_discharge_m3': sum(b.channel_discharge_m3 for b in self.daily_budgets),
            'pumping_m3': sum(b.pumping_m3 for b in self.daily_budgets),
            'drain_outflow_m3': sum(b.drain_outflow_m3 for b in self.daily_budgets),
            'total_storage_change_m3': sum(b.modflow_storage_change_m3 for b in self.daily_budgets),
            'cumulative_error_m3': sum(b.compute_mass_balance_error() for b in self.daily_budgets)
        }
        
        return cumulative
    
    def get_average_daily_budget(self) -> Dict[str, float]:
        """
        Get average daily water budget.
        
        Returns:
            Dictionary with average daily values (m³/day)
        """
        if not self.daily_budgets:
            return {}
        
        n_days = len(self.daily_budgets)
        cumulative = self.get_cumulative_budget()
        
        return {key: value / n_days for key, value in cumulative.items()}
    
    def compute_total_mass_balance_error(self) -> float:
        """
        Compute total mass balance error over all timesteps.
        
        Returns:
            Cumulative mass balance error (m³)
        """
        return sum(b.compute_mass_balance_error() for b in self.daily_budgets)
    
    def get_max_daily_error(self) -> tuple:
        """
        Find day with maximum mass balance error.
        
        Returns:
            (date, error_m3, percent_error)
        """
        if not self.daily_budgets:
            return None, 0.0, 0.0
        
        max_budget = max(
            self.daily_budgets,
            key=lambda b: abs(b.compute_mass_balance_error())
        )
        
        return (
            max_budget.date,
            max_budget.compute_mass_balance_error(),
            max_budget.get_mass_balance_percent_error()
        )
    
    def validate_mass_balance(self, tolerance_percent: float = 1.0) -> bool:
        """
        Validate mass balance within tolerance.
        
        Args:
            tolerance_percent: Maximum allowable percent error (default 1%)
        
        Returns:
            True if all days within tolerance
        """
        for budget in self.daily_budgets:
            percent_error = budget.get_mass_balance_percent_error()
            if percent_error > tolerance_percent:
                self.logger.error(
                    f"Mass balance validation failed on {budget.date}: "
                    f"{percent_error:.2f}% > {tolerance_percent}%"
                )
                return False
        
        self.logger.info(
            f"Mass balance validated: all {len(self.daily_budgets)} days "
            f"within {tolerance_percent}% tolerance"
        )
        return True
    
    def print_summary(self) -> None:
        """Print summary of water budget."""
        if not self.daily_budgets:
            print("No water budget data available")
            return
        
        cumulative = self.get_cumulative_budget()
        average = self.get_average_daily_budget()
        max_date, max_error, max_percent = self.get_max_daily_error()
        
        print("\n" + "="*60)
        print("WATER BUDGET SUMMARY")
        print("="*60)
        print(f"Simulation period: {self.daily_budgets[0].date} to {self.daily_budgets[-1].date}")
        print(f"Number of days: {len(self.daily_budgets)}")
        print()
        
        print("CUMULATIVE BUDGET (m³):")
        print(f"  Recharge:             {cumulative['recharge_m3']:>15,.0f}")
        print(f"  Unsatisfied ET:       {cumulative['unsatisfied_et_m3']:>15,.0f}")
        print(f"  Channel discharge:    {cumulative['channel_discharge_m3']:>15,.0f}")
        print(f"  Pumping:              {cumulative['pumping_m3']:>15,.0f}")
        print(f"  Drain outflow:        {cumulative['drain_outflow_m3']:>15,.0f}")
        print(f"  Storage change:       {cumulative['total_storage_change_m3']:>15,.0f}")
        print(f"  Cumulative error:     {cumulative['cumulative_error_m3']:>15,.0f}")
        print()
        
        print("AVERAGE DAILY BUDGET (m³/day):")
        print(f"  Recharge:             {average['recharge_m3']:>15,.0f}")
        print(f"  Unsatisfied ET:       {average['unsatisfied_et_m3']:>15,.0f}")
        print(f"  Channel discharge:    {average['channel_discharge_m3']:>15,.0f}")
        print()
        
        print("MASS BALANCE QUALITY:")
        print(f"  Max daily error:      {max_error:>15,.2f} m³ on {max_date}")
        print(f"  Max percent error:    {max_percent:>15,.2f} %")
        print("="*60 + "\n")
