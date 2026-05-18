"""
Example: Basic SWAT+ - MODFLOW 6 Coupling Simulation

This example demonstrates:
1. Loading model directories
2. Setting up geographic connections
3. Configuring coupling parameters
4. Running a coupled simulation
5. Accessing results

References:
    Bailey et al. (2025) – SWAT+MODFLOW, GMD 18, 5681-5697.
"""

import logging
from pathlib import Path
from swatplus_modflow import SWATPlusMODFLOWCoupler


def main():
    """Run example coupling simulation"""
    
    # Optional: enable logging to see progress
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    
    # Define model directories
    modflow_dir = Path("./example_data/modflow")
    swatplus_dir = Path("./example_data/swatplus")
    geo_connections = Path("./example_data/geo_connections.shp")
    output_dir = Path("./results")
    
    # Initialize coupler (optionally pass a config.yaml)
    print("Initializing SWAT+ - MODFLOW 6 coupler...")
    coupler = SWATPlusMODFLOWCoupler(
        modflow_dir=modflow_dir,
        swatplus_dir=swatplus_dir,
        geo_connections_file=geo_connections,
        output_dir=output_dir,
        # config_file="config.yaml",  # uncomment to use custom settings
    )
    
    # Load geographic connections (HRU↔cell, Channel↔cell, etc.)
    print("Loading geographic connections...")
    coupler.load_geographic_connections()
    
    # Run coupled simulation
    print("Running coupled simulation...")
    coupler.run(
        start_date="2020-01-01",
        end_date="2020-12-31"
    )
    
    # Get results
    print("\nSimulation completed!")
    print("=" * 60)
    
    # Daily flows
    df_flows = coupler.get_daily_flows()
    if not df_flows.empty:
        print("\nDaily Flow Summary (first 10 days):")
        print(df_flows.head(10).to_string(index=False))
    else:
        print("\nNo daily flow data produced.")
    
    # Water balance
    print("\n" + coupler.get_water_balance())
    
    # Save daily flows
    if not df_flows.empty:
        output_csv = output_dir / "daily_flows_summary.csv"
        df_flows.to_csv(output_csv, index=False)
        print(f"\nDaily flows saved to: {output_csv}")


if __name__ == "__main__":
    main()
