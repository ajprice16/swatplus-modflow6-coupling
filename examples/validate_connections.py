"""
Utility script to validate GIS connections file for SWAT+ - MODFLOW 6 coupling.

Run this script after creating your connections file in QGIS to verify:
- Required attributes are present
- Data types are correct
- HRU fractions sum to ~1.0
- MODFLOW indices are valid
- No duplicate connections

Usage:
    python validate_connections.py connections.gpkg --modflow-grid 50 100 3
"""

import sys
from pathlib import Path
import argparse
from collections import defaultdict

try:
    import geopandas as gpd
    import pandas as pd
except ImportError:
    print("Error: geopandas not installed. Install with: pip install geopandas")
    sys.exit(1)


def validate_connections(
    connections_file: Path,
    nrow: int = None,
    ncol: int = None,
    nlay: int = None
) -> bool:
    """
    Validate connections file structure and data.
    
    Args:
        connections_file: Path to connections shapefile or geopackage
        nrow: Number of MODFLOW rows (optional)
        ncol: Number of MODFLOW columns (optional)
        nlay: Number of MODFLOW layers (optional)
    
    Returns:
        True if validation passes
    """
    print("="*60)
    print("VALIDATING GIS CONNECTIONS FILE")
    print("="*60)
    print(f"File: {connections_file}")
    print()
    
    # Load connections file
    try:
        gdf = gpd.read_file(connections_file)
        print(f"✓ File loaded successfully: {len(gdf)} features")
    except Exception as e:
        print(f"✗ Error loading file: {e}")
        return False
    
    # Check required attributes
    print()
    print("Checking required attributes...")
    required_attrs = ['swat_obj_type', 'swat_obj_id', 'mf_layer', 'mf_row', 'mf_col']
    optional_attrs = ['fraction', 'length_m']
    
    missing = [attr for attr in required_attrs if attr not in gdf.columns]
    if missing:
        print(f"✗ Missing required attributes: {missing}")
        return False
    else:
        print(f"✓ All required attributes present: {required_attrs}")
    
    # Check optional attributes
    has_fraction = 'fraction' in gdf.columns
    has_length = 'length_m' in gdf.columns
    print(f"  Optional attributes: fraction={has_fraction}, length_m={has_length}")
    
    # Validate data types
    print()
    print("Checking data types...")
    try:
        assert gdf['swat_obj_type'].dtype == object, "swat_obj_type must be string"
        assert gdf['swat_obj_id'].dtype in [int, 'int32', 'int64'], "swat_obj_id must be integer"
        assert gdf['mf_layer'].dtype in [int, 'int32', 'int64'], "mf_layer must be integer"
        assert gdf['mf_row'].dtype in [int, 'int32', 'int64'], "mf_row must be integer"
        assert gdf['mf_col'].dtype in [int, 'int32', 'int64'], "mf_col must be integer"
        print("✓ Data types correct")
    except AssertionError as e:
        print(f"✗ Data type error: {e}")
        return False
    
    # Check object types
    print()
    print("Checking object types...")
    obj_types = gdf['swat_obj_type'].unique()
    valid_types = ['hru', 'channel', 'reservoir']
    invalid = [t for t in obj_types if t not in valid_types]
    
    if invalid:
        print(f"✗ Invalid object types found: {invalid}")
        print(f"  Valid types: {valid_types}")
        return False
    else:
        print(f"✓ Object types valid: {list(obj_types)}")
    
    # Count connections by type
    type_counts = gdf['swat_obj_type'].value_counts()
    for obj_type, count in type_counts.items():
        print(f"  {obj_type}: {count} connections")
    
    # Validate MODFLOW indices
    print()
    print("Checking MODFLOW grid indices...")
    
    if nrow and ncol and nlay:
        out_of_bounds = gdf[
            (gdf['mf_row'] < 1) | (gdf['mf_row'] > nrow) |
            (gdf['mf_col'] < 1) | (gdf['mf_col'] > ncol) |
            (gdf['mf_layer'] < 1) | (gdf['mf_layer'] > nlay)
        ]
        
        if len(out_of_bounds) > 0:
            print(f"✗ {len(out_of_bounds)} connections have out-of-bounds indices")
            print(f"  Valid ranges: layer 1-{nlay}, row 1-{nrow}, col 1-{ncol}")
            print(out_of_bounds[['swat_obj_type', 'swat_obj_id', 'mf_layer', 'mf_row', 'mf_col']].head())
            return False
        else:
            print(f"✓ All indices within bounds (layer 1-{nlay}, row 1-{nrow}, col 1-{ncol})")
    else:
        print("⚠ Grid dimensions not provided, skipping bounds check")
        print(f"  Range found: layer {gdf['mf_layer'].min()}-{gdf['mf_layer'].max()}, "
              f"row {gdf['mf_row'].min()}-{gdf['mf_row'].max()}, "
              f"col {gdf['mf_col'].min()}-{gdf['mf_col'].max()}")
    
    # Validate HRU fractions
    if has_fraction:
        print()
        print("Checking HRU fraction sums...")
        
        hru_gdf = gdf[gdf['swat_obj_type'] == 'hru'].copy()
        if len(hru_gdf) > 0:
            fraction_sums = hru_gdf.groupby('swat_obj_id')['fraction'].sum()
            
            # Check for sums far from 1.0
            tolerance = 0.05
            bad_sums = fraction_sums[(fraction_sums < 1.0 - tolerance) | (fraction_sums > 1.0 + tolerance)]
            
            if len(bad_sums) > 0:
                print(f"⚠ {len(bad_sums)} HRUs have fraction sums outside tolerance (1.0 ± {tolerance}):")
                for hru_id, fsum in bad_sums.head(10).items():
                    print(f"  HRU {hru_id}: sum = {fsum:.3f}")
                if len(bad_sums) > 10:
                    print(f"  ... and {len(bad_sums) - 10} more")
            else:
                print(f"✓ All {len(fraction_sums)} HRU fraction sums within tolerance (1.0 ± {tolerance})")
                print(f"  Mean: {fraction_sums.mean():.4f}, Std: {fraction_sums.std():.4f}")
    
    # Validate channel lengths
    if has_length:
        print()
        print("Checking channel lengths...")
        
        channel_gdf = gdf[gdf['swat_obj_type'] == 'channel'].copy()
        if len(channel_gdf) > 0:
            invalid_lengths = channel_gdf[channel_gdf['length_m'] <= 0]
            
            if len(invalid_lengths) > 0:
                print(f"✗ {len(invalid_lengths)} channel connections have invalid length (≤ 0)")
                return False
            else:
                print(f"✓ All {len(channel_gdf)} channel lengths valid")
                print(f"  Range: {channel_gdf['length_m'].min():.1f} - {channel_gdf['length_m'].max():.1f} m")
                print(f"  Mean: {channel_gdf['length_m'].mean():.1f} m")
    
    # Check for duplicates
    print()
    print("Checking for duplicate connections...")
    
    duplicates = gdf.groupby(['swat_obj_type', 'swat_obj_id', 'mf_layer', 'mf_row', 'mf_col']).size()
    duplicates = duplicates[duplicates > 1]
    
    if len(duplicates) > 0:
        print(f"⚠ {len(duplicates)} duplicate connections found")
        print("  (same SWAT object connected to same MODFLOW cell multiple times)")
        for idx, count in duplicates.head(5).items():
            print(f"  {idx}: {count} connections")
    else:
        print("✓ No duplicate connections")
    
    # Summary statistics
    print()
    print("="*60)
    print("SUMMARY")
    print("="*60)
    
    # Count unique SWAT objects
    for obj_type in obj_types:
        obj_gdf = gdf[gdf['swat_obj_type'] == obj_type]
        n_unique = obj_gdf['swat_obj_id'].nunique()
        n_connections = len(obj_gdf)
        print(f"{obj_type.capitalize()}s:")
        print(f"  Unique objects: {n_unique}")
        print(f"  Total connections: {n_connections}")
        print(f"  Average connections per object: {n_connections/n_unique:.2f}")
    
    # Count unique MODFLOW cells
    unique_cells = gdf.groupby(['mf_layer', 'mf_row', 'mf_col']).size()
    print(f"\nMODFLOW cells:")
    print(f"  Unique cells with connections: {len(unique_cells)}")
    print(f"  Average connections per cell: {len(gdf)/len(unique_cells):.2f}")
    
    print()
    print("="*60)
    print("✓ VALIDATION COMPLETE")
    print("="*60)
    
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Validate GIS connections file for SWAT+ - MODFLOW 6 coupling"
    )
    parser.add_argument(
        'connections_file',
        type=Path,
        help="Path to connections shapefile or geopackage"
    )
    parser.add_argument(
        '--modflow-grid',
        nargs=3,
        type=int,
        metavar=('NROW', 'NCOL', 'NLAY'),
        help="MODFLOW grid dimensions (NROW NCOL NLAY) for bounds checking"
    )
    
    args = parser.parse_args()
    
    if not args.connections_file.exists():
        print(f"Error: File not found: {args.connections_file}")
        return 1
    
    # Validate
    nrow, ncol, nlay = args.modflow_grid if args.modflow_grid else (None, None, None)
    
    success = validate_connections(
        args.connections_file,
        nrow=nrow,
        ncol=ncol,
        nlay=nlay
    )
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
