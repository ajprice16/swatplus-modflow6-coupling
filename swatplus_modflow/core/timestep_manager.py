"""Daily timestep manager"""

from datetime import datetime, timedelta
from typing import Generator


class DailyTimestepManager:
    """Manages daily timestep iteration"""
    
    def __init__(self, start_date: datetime, end_date: datetime, timestep_days: int = 1):
        """
        Initialize timestep manager.
        
        Args:
            start_date: Simulation start date
            end_date: Simulation end date
            timestep_days: Number of days per timestep (default 1)
        """
        self.start_date = start_date
        self.end_date = end_date
        self.timestep_days = timestep_days
        self.current_date = start_date
        self.day_count = 0
    
    def reset(self) -> None:
        """Reset to start date"""
        self.current_date = self.start_date
        self.day_count = 0
    
    def advance(self) -> bool:
        """
        Advance to next timestep.
        
        Returns:
            True if successful, False if past end date
        """
        self.current_date += timedelta(days=self.timestep_days)
        self.day_count += self.timestep_days
        return self.current_date <= self.end_date
    
    def iterate(self) -> Generator[datetime, None, None]:
        """Iterate through all timesteps"""
        current = self.start_date
        while current <= self.end_date:
            yield current
            current += timedelta(days=self.timestep_days)
    
    @property
    def is_complete(self) -> bool:
        """Check if simulation period is complete"""
        return self.current_date > self.end_date
    
    @property
    def progress_fraction(self) -> float:
        """Fraction of simulation complete (0-1)"""
        total_days = (self.end_date - self.start_date).days
        elapsed_days = (self.current_date - self.start_date).days
        return min(1.0, max(0.0, elapsed_days / total_days))
