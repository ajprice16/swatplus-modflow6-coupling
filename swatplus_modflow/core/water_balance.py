"""Water balance tracking"""

from datetime import datetime
from typing import Dict, List
import numpy as np


class WaterBalanceTracker:
    """Tracks water balance throughout simulation"""
    
    def __init__(self):
        self.dates: List[datetime] = []
        self.recharge_in: List[float] = []  # m³/day
        self.et_out: List[float] = []
        self.pumping_out: List[float] = []
        self.baseflow_out: List[float] = []
        self.errors: List[float] = []
    
    def update(
        self,
        date: datetime,
        recharge_in: float = 0.0,
        et_out: float = 0.0,
        pumping_out: float = 0.0,
        baseflow_out: float = 0.0
    ) -> None:
        """Record daily balance"""
        self.dates.append(date)
        self.recharge_in.append(recharge_in)
        self.et_out.append(et_out)
        self.pumping_out.append(pumping_out)
        self.baseflow_out.append(baseflow_out)
        
        # Calculate balance error (should be minimal)
        total_out = et_out + pumping_out + baseflow_out
        error = abs(recharge_in - total_out)
        self.errors.append(error)
    
    def get_report(self) -> str:
        """Generate water balance report"""
        if not self.dates:
            return "No data available"
        
        total_recharge = sum(self.recharge_in)
        total_et = sum(self.et_out)
        total_pumping = sum(self.pumping_out)
        total_baseflow = sum(self.baseflow_out)
        max_error = max(self.errors)
        avg_error = np.mean(self.errors)
        
        report = f"""
WATER BALANCE REPORT
{'='*60}
Simulation Period: {self.dates[0]} to {self.dates[-1]}
Number of Days: {len(self.dates)}

TOTAL FLUXES (m³):
  Recharge (in):        {total_recharge:>15,.2f}
  ET (out):             {total_et:>15,.2f}
  Pumping (out):        {total_pumping:>15,.2f}
  Baseflow (out):       {total_baseflow:>15,.2f}
  Total Out:            {total_et + total_pumping + total_baseflow:>15,.2f}

BALANCE ERROR:
  Maximum Daily Error:  {max_error:>15,.2f} m³/day
  Average Daily Error:  {avg_error:>15,.6f} m³/day

DAILY AVERAGES (m³/day):
  Recharge:             {total_recharge / len(self.dates):>15,.2f}
  ET:                   {total_et / len(self.dates):>15,.2f}
  Pumping:              {total_pumping / len(self.dates):>15,.2f}
  Baseflow:             {total_baseflow / len(self.dates):>15,.2f}

{'='*60}
"""
        return report
