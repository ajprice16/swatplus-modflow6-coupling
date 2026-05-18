"""
Example workflow for SWAT+ - MODFLOW 6 coupling.

This example demonstrates:
1. Configuration setup
2. Model initialization
3. Daily coupling loop
4. State management and output generation
"""

from datetime import datetime, timedelta
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from swatplus_modflow.core.simulator import SWATPlusMODFLOWCoupler, CouplingSettings
from swatplus_modflow.utils.water_budget import WaterBudgetTracker


def main():
    """
    Run example SWAT+ - MODFLOW 6 coupling simulation.
    
    This example uses synthetic model files (not included).
    Replace paths below with actual model directories.
    """
    
    # ========== STEP 1: Configuration ==========
    print("="*60)
    print("SWAT+ - MODFLOW 6 Coupling Example")
    print("="*60)
    print()
    
    # Define simulation period
    start_date = datetime(2015, 1, 1)
    end_date = datetime(2020, 12, 31)
    
    # Define model directories
    project_dir = Path(__file__).parent.parent
    swat_dir = project_dir / "example_models" / "swat_model"
    modflow_dir = project_dir / "example_models" / "modflow_model"
    geo_file = project_dir / "example_models" / "connections.gpkg"
    output_dir = project_dir / "outputs" / f"run_{datetime.now():%Y%m%d_%H%M%S}"
    
    # Create coupling settings
    settings = CouplingSettings(
        # Required paths
        modflow_dir=modflow_dir,
        swatplus_dir=swat_dir,
        geo_connections_file=geo_file,
        output_dir=output_dir,
        
        # Time settings
        start_date=start_date,
        end_date=end_date,
        timestep_days=1,
        
        # Coupling parameters
        recharge_delay_days=10.0,       # Vadose zone delay
        et_extinction_depth_m=3.0,      # Max ET extraction depth
        
        # Solver settings
        modflow_convergence_tol=1e-4,
        max_iterations=10000,
        
        # Output settings
        save_state_interval_days=30,    # Save state every 30 days
        save_cell_fluxes=True,
        save_daily_summary=True
    )
    
    print(f"Simulation period: {start_date} to {end_date}")
    print(f"Total days: {(end_date - start_date).days + 1}")
    print(f"Output directory: {output_dir}")
    print()
    
    # ========== STEP 2: Initialize Coupler ==========
    print("Initializing coupled models...")
    
    try:
        coupler = SWATPlusMODFLOWCoupler(settings)
        print("✓ Models initialized successfully")
        print()
    except Exception as e:
        print(f"✗ Error initializing models: {e}")
        print()
        print("Note: This example requires actual SWAT+ and MODFLOW 6 model files.")
        print("Replace the paths in this script with your model directories.")
        return 1
    
    # ========== STEP 3: Run Coupling Simulation ==========
    print("Starting coupled simulation...")
    print()
    
    try:
        results = coupler.run()
        print()
        print("✓ Simulation completed successfully")
        print()
    except Exception as e:
        print(f"✗ Error during simulation: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # ========== STEP 4: Analyze Results ==========
    print("="*60)
    print("RESULTS SUMMARY")
    print("="*60)
    print()
    
    # Print water budget summary
    if hasattr(coupler, 'budget_tracker'):
        coupler.budget_tracker.print_summary()
    
    # Print output file locations
    print("OUTPUT FILES:")
    print(f"  Daily summary:    {output_dir / 'daily_summary.csv'}")
    print(f"  Water balance:    {output_dir / 'water_balance.csv'}")
    print(f"  Cell fluxes:      {output_dir / 'cell_fluxes.csv'}")
    print(f"  Final state:      {output_dir / 'final_state.h5'}")
    print()
    
    print("="*60)
    print("COUPLING COMPLETED")
    print("="*60)
    
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
