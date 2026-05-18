"""
QGIS Python Console Script for Creating SWAT+ - MODFLOW Connections

Instructions:
1. Load your HRU polygons and MODFLOW grid into QGIS
2. Open Python Console (Plugins → Python Console)
3. Click "Show Editor" button
4. Load this script
5. Update the parameters below
6. Click "Run Script"

This script will:
- Intersect HRUs with MODFLOW grid
- Calculate area fractions
- Generate connections table with proper attributes
- Export to GeoPackage
"""

from qgis.core import (
    QgsVectorLayer, QgsField, QgsFeature, QgsGeometry, 
    QgsProject, QgsVectorFileWriter, QgsWkbTypes
)
from PyQt5.QtCore import QVariant
import processing

# ============================================================
# CONFIGURATION - SRV Phoenix Model Parameters
# ============================================================

# Input layers (must be loaded in QGIS first)
# After QSWAT+ setup: load hrus1.shp from the QSWAT+ project
# Grid: load modflow_grid_active.gpkg from models/connections/
HRU_LAYER_NAME = 'hrus1'             # QSWAT+ output HRU layer
CHANNEL_LAYER_NAME = 'rivs1'         # QSWAT+ output channel/reach layer
GRID_LAYER_NAME = 'modflow_grid_active'  # From create_modflow_grid_gpkg.py

# SRV MODFLOW grid dimensions
NROW = 125   # Number of rows
NCOL = 222   # Number of columns
NLAY = 3     # Number of layers

# Output file
OUTPUT_FILE = 'D:/Aprice/swatplus_modflow6_coupling/models/connections/connections.gpkg'

# Processing options
MIN_FRACTION = 0.05  # Minimum area fraction to include (filter small slivers)
DEFAULT_LAYER = 1    # Default MODFLOW layer for connections (Layer 1 = UAU)

# ============================================================
# PROCESSING CODE
# ============================================================

def create_connections():
    """Main function to create connections file."""
    
    print("="*60)
    print("Creating SWAT+ - MODFLOW Connections")
    print("="*60)
    
    # Get layers
    hru_layer = QgsProject.instance().mapLayersByName(HRU_LAYER_NAME)
    grid_layer = QgsProject.instance().mapLayersByName(GRID_LAYER_NAME)
    
    if not hru_layer:
        print(f"ERROR: HRU layer '{HRU_LAYER_NAME}' not found!")
        return
    if not grid_layer:
        print(f"ERROR: Grid layer '{GRID_LAYER_NAME}' not found!")
        return
    
    hru_layer = hru_layer[0]
    grid_layer = grid_layer[0]
    
    print(f"HRU layer: {hru_layer.featureCount()} features")
    print(f"Grid layer: {grid_layer.featureCount()} features")
    print()
    
    # Create output layer
    crs = hru_layer.crs()
    connections = QgsVectorLayer(f"Polygon?crs={crs.authid()}", "connections", "memory")
    provider = connections.dataProvider()
    
    # Add attribute fields
    provider.addAttributes([
        QgsField("swat_obj_type", QVariant.String),
        QgsField("swat_obj_id", QVariant.Int),
        QgsField("mf_layer", QVariant.Int),
        QgsField("mf_row", QVariant.Int),
        QgsField("mf_col", QVariant.Int),
        QgsField("fraction", QVariant.Double),
        QgsField("length_m", QVariant.Double),
    ])
    connections.updateFields()
    
    print("Processing HRU intersections...")
    process_hru_intersections(hru_layer, grid_layer, connections)
    
    # Process channels if available
    channel_layer = QgsProject.instance().mapLayersByName(CHANNEL_LAYER_NAME)
    if channel_layer:
        print("\nProcessing channel intersections...")
        process_channel_intersections(channel_layer[0], grid_layer, connections)
    else:
        print(f"\nWARNING: Channel layer '{CHANNEL_LAYER_NAME}' not found, skipping")
    
    # Save to file
    print(f"\nSaving connections to {OUTPUT_FILE}...")
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.fileEncoding = "UTF-8"
    
    error = QgsVectorFileWriter.writeAsVectorFormatV2(
        connections,
        OUTPUT_FILE,
        QgsProject.instance().transformContext(),
        options
    )
    
    if error[0] == QgsVectorFileWriter.NoError:
        print(f"✓ SUCCESS: Connections saved to {OUTPUT_FILE}")
        print(f"  Total features: {connections.featureCount()}")
    else:
        print(f"✗ ERROR saving file: {error[1]}")
    
    # Add to QGIS
    QgsProject.instance().addMapLayer(connections)
    print("\n✓ Connections layer added to QGIS")


def process_hru_intersections(hru_layer, grid_layer, connections):
    """Process HRU-grid intersections and calculate fractions."""
    
    # Run intersection
    result = processing.run("native:intersection", {
        'INPUT': hru_layer,
        'OVERLAY': grid_layer,
        'INPUT_FIELDS': [],
        'OVERLAY_FIELDS': [],
        'OUTPUT': 'memory:'
    })
    
    intersect_layer = result['OUTPUT']
    print(f"  Intersection created: {intersect_layer.featureCount()} features")
    
    # Calculate areas and fractions
    hru_areas = {}  # Store total HRU area
    
    # First pass: calculate total HRU areas
    for feat in hru_layer.getFeatures():
        hru_id = feat['hru_id']  # Adjust field name as needed
        hru_areas[hru_id] = feat.geometry().area()
    
    # Second pass: calculate fractions and create connections
    features_to_add = []
    
    for feat in intersect_layer.getFeatures():
        hru_id = feat['hru_id']  # Adjust field name
        grid_id = feat['grid_id']  # Adjust field name - or calculate from geometry
        
        # Calculate fraction
        intersect_area = feat.geometry().area()
        fraction = intersect_area / hru_areas[hru_id] if hru_id in hru_areas else 0
        
        # Filter small fractions
        if fraction < MIN_FRACTION:
            continue
        
        # Calculate grid row/col from grid_id or geometry
        # Assuming grid_id = (row-1) * NCOL + (col-1)
        row = (grid_id // NCOL) + 1
        col = (grid_id % NCOL) + 1
        
        # Create feature
        new_feat = QgsFeature(connections.fields())
        new_feat.setGeometry(feat.geometry())
        new_feat.setAttribute('swat_obj_type', 'hru')
        new_feat.setAttribute('swat_obj_id', hru_id)
        new_feat.setAttribute('mf_layer', DEFAULT_LAYER)
        new_feat.setAttribute('mf_row', row)
        new_feat.setAttribute('mf_col', col)
        new_feat.setAttribute('fraction', round(fraction, 4))
        new_feat.setAttribute('length_m', None)
        
        features_to_add.append(new_feat)
    
    # Add features to connections layer
    connections.dataProvider().addFeatures(features_to_add)
    connections.updateExtents()
    
    print(f"  ✓ Added {len(features_to_add)} HRU connections")


def process_channel_intersections(channel_layer, grid_layer, connections):
    """Process channel-grid intersections and calculate lengths."""
    
    # Run intersection
    result = processing.run("native:intersection", {
        'INPUT': channel_layer,
        'OVERLAY': grid_layer,
        'INPUT_FIELDS': [],
        'OVERLAY_FIELDS': [],
        'OUTPUT': 'memory:'
    })
    
    intersect_layer = result['OUTPUT']
    print(f"  Intersection created: {intersect_layer.featureCount()} features")
    
    features_to_add = []
    
    for feat in intersect_layer.getFeatures():
        channel_id = feat['reach_id']  # Adjust field name as needed
        grid_id = feat['grid_id']       # Adjust field name
        
        # Calculate segment length
        length = feat.geometry().length()
        
        # Calculate grid row/col
        row = (grid_id // NCOL) + 1
        col = (grid_id % NCOL) + 1
        
        # Create feature
        new_feat = QgsFeature(connections.fields())
        new_feat.setGeometry(feat.geometry())
        new_feat.setAttribute('swat_obj_type', 'channel')
        new_feat.setAttribute('swat_obj_id', channel_id)
        new_feat.setAttribute('mf_layer', DEFAULT_LAYER)
        new_feat.setAttribute('mf_row', row)
        new_feat.setAttribute('mf_col', col)
        new_feat.setAttribute('fraction', None)
        new_feat.setAttribute('length_m', round(length, 2))
        
        features_to_add.append(new_feat)
    
    # Add features
    connections.dataProvider().addFeatures(features_to_add)
    connections.updateExtents()
    
    print(f"  ✓ Added {len(features_to_add)} channel connections")


# Run the script
if __name__ == '__main__':
    create_connections()
