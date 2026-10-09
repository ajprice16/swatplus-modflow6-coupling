# GIS Preprocessing Guide for SWAT+ - MODFLOW 6 Coupling

This guide explains how to prepare the geographic connections file in QGIS that maps SWAT+ objects (HRUs, channels, reservoirs) to MODFLOW 6 grid cells.

## Overview

The coupling framework requires a **geographic connections file** (shapefile or geopackage) that defines spatial relationships between:
- SWAT+ Hydrologic Response Units (HRUs) → MODFLOW cells
- SWAT+ channels/reaches → MODFLOW cells
- SWAT+ reservoirs → MODFLOW cells (optional)

This file is read by the `GeographicMapper` class to distribute fluxes between models.

---

## Required Attributes

### Connection File Schema

The connections file must contain the following attributes:

| Attribute Name    | Type    | Description                                           | Example Values |
|-------------------|---------|-------------------------------------------------------|----------------|
| `swat_obj_type`   | String  | Type of SWAT+ object: "hru", "channel", "reservoir"  | hru            |
| `swat_obj_id`     | Integer | SWAT+ object ID (HRU number, channel ID, etc.)       | 1, 2, 3...     |
| `mf_layer`        | Integer | MODFLOW layer number (1-based)                       | 1, 2, 3...     |
| `mf_row`          | Integer | MODFLOW row number (1-based)                         | 1-NROW         |
| `mf_col`          | Integer | MODFLOW column number (1-based)                      | 1-NCOL         |
| `fraction`        | Float   | Spatial overlap fraction (for HRUs, 0-1)             | 0.75           |
| `length_m`        | Float   | Channel length within cell (for channels, meters)    | 1500.0         |

### Geometry Type

- **Points** or **Polygons** are both acceptable
- Polygons are recommended for better visualization
- One feature per SWAT+ object ↔ MODFLOW cell connection

---

## QGIS Workflow

### Step 1: Load Base Layers

1. **Load SWAT+ spatial data:**
   - HRU polygons (from SWAT+ model setup)
   - Channel/reach lines (from SWAT+ model setup)
   - Subbasin boundaries

2. **Load MODFLOW 6 grid:**
   - Create grid from DIS file or model extent
   - Use "Create Grid" tool (Vector → Research Tools → Create Grid)
   - Grid should match MODFLOW NROW × NCOL dimensions

### Step 2: Calculate Intersections

#### For HRUs → MODFLOW Cells

1. **Intersect HRU polygons with MODFLOW grid:**
   ```
   Vector → Geoprocessing Tools → Intersection
   Input layer: HRU polygons
   Overlay layer: MODFLOW grid
   ```

2. **Calculate area fractions:**
   - Add field `area_m2` = $area
   - Group by original HRU ID
   - Calculate `fraction` = area_m2 / total_hru_area

3. **Filter small overlaps (optional):**
   - Remove connections where `fraction < 0.05` (less than 5% overlap)

#### For Channels → MODFLOW Cells

1. **Intersect channel lines with MODFLOW grid:**
   ```
   Vector → Geoprocessing Tools → Intersection
   Input layer: Channel reaches
   Overlay layer: MODFLOW grid
   ```

2. **Calculate segment lengths:**
   - Add field `length_m` = $length
   - Each channel segment within a cell becomes one connection

### Step 3: Build Connections Table

1. **Create new geopackage layer:**
   ```
   Layer → Create Layer → New GeoPackage Layer
   ```

2. **Add required fields:**
   - Open attribute table → Toggle editing
   - Add fields matching schema above

3. **Populate MODFLOW grid indices:**
   ```python
   # In Field Calculator, calculate mf_row and mf_col from grid
   # Example for grid ID stored as "grid_id":
   mf_row = floor("grid_id" / NCOL) + 1
   mf_col = ("grid_id" % NCOL) + 1
   ```

4. **Set object types:**
   - HRU connections: `swat_obj_type = 'hru'`
   - Channel connections: `swat_obj_type = 'channel'`

### Step 4: Quality Control

**Check for issues:**

- [ ] Each HRU has at least one connection
- [ ] Sum of fractions per HRU ≈ 1.0 (allow ±0.05 tolerance)
- [ ] All MODFLOW indices within valid range (1 to NROW/NCOL)
- [ ] No duplicate connections (same SWAT object + MODFLOW cell)
- [ ] Channel lengths > 0

**Validation query:**
```sql
-- Check HRU fraction sums in QGIS DB Manager
SELECT swat_obj_id, SUM(fraction) as total_fraction
FROM connections
WHERE swat_obj_type = 'hru'
GROUP BY swat_obj_id
HAVING ABS(total_fraction - 1.0) > 0.05;
```

### Step 5: Export Final File

**Save as GeoPackage or Shapefile:**
```
Right-click layer → Export → Save Features As
Format: GeoPackage (.gpkg) or ESRI Shapefile
Coordinate Reference System: Match MODFLOW model CRS
```

**Recommended:** GeoPackage for better attribute type handling

---

## Example Connections Table

### Sample Data Structure

| swat_obj_type | swat_obj_id | mf_layer | mf_row | mf_col | fraction | length_m |
|---------------|-------------|----------|--------|--------|----------|----------|
| hru           | 1           | 1        | 10     | 15     | 0.65     | NULL     |
| hru           | 1           | 1        | 10     | 16     | 0.35     | NULL     |
| hru           | 2           | 1        | 11     | 15     | 1.00     | NULL     |
| channel       | 101         | 1        | 10     | 15     | NULL     | 450.0    |
| channel       | 101         | 1        | 10     | 16     | NULL     | 550.0    |
| channel       | 101         | 1        | 11     | 16     | NULL     | 300.0    |

**Notes:**
- HRU 1 overlaps 2 MODFLOW cells (65% in cell [10,15], 35% in cell [10,16])
- HRU 2 is fully within one cell
- Channel 101 flows through 3 cells with varying segment lengths

---

## Advanced: Python Processing Script

For large models, use QGIS Python console:

```python
from qgis.core import QgsVectorLayer, QgsField, QgsFeature
from PyQt5.QtCore import QVariant

# Load layers
hru_layer = QgsProject.instance().mapLayersByName('HRU_polygons')[0]
grid_layer = QgsProject.instance().mapLayersByName('MODFLOW_grid')[0]

# Create connections layer
connections = QgsVectorLayer("Polygon?crs=EPSG:32614", "connections", "memory")
provider = connections.dataProvider()

# Add fields
provider.addAttributes([
    QgsField("swat_obj_type", QVariant.String),
    QgsField("swat_obj_id", QVariant.Int),
    QgsField("mf_layer", QVariant.Int),
    QgsField("mf_row", QVariant.Int),
    QgsField("mf_col", QVariant.Int),
    QgsField("fraction", QVariant.Double)
])
connections.updateFields()

# Process intersections
processing.run("native:intersection", {
    'INPUT': hru_layer,
    'OVERLAY': grid_layer,
    'OUTPUT': 'memory:intersections'
})

# Calculate fractions and populate connections
# (Add your calculation logic here)

# Save to file
QgsVectorFileWriter.writeAsVectorFormat(
    connections,
    '/path/to/connections.gpkg',
    'UTF-8',
    connections.crs(),
    'GPKG'
)
```

---

## Troubleshooting

### Common Issues

**Issue:** Sum of HRU fractions ≠ 1.0
- **Cause:** Small gaps or overlaps in intersection
- **Fix:** Use buffer of 0.1m on HRU polygons before intersection

**Issue:** Channels not connecting to any cells
- **Cause:** Coordinate reference system mismatch
- **Fix:** Ensure all layers use same CRS (reproject if needed)

**Issue:** Too many connections (performance concern)
- **Cause:** Fine MODFLOW grid relative to HRU size
- **Fix:** Filter connections with fraction < 0.05

**Issue:** Missing connections for some HRUs
- **Cause:** HRUs outside MODFLOW model domain
- **Fix:** Clip HRU layer to MODFLOW active domain before intersection

---

## Testing the Connections File

After creating the connections file, test it with the coupling framework:

```python
from swatplus_modflow.coupling.geographic_mapper import GeographicMapper

# Load connections
mapper = GeographicMapper()
mapper.load_connections('path/to/connections.gpkg')

# Verify
print(f"Total connections: {len(mapper.connections)}")
print(f"HRU connections: {sum(1 for c in mapper.connections if c['swat_obj_type'] == 'hru')}")
print(f"Channel connections: {sum(1 for c in mapper.connections if c['swat_obj_type'] == 'channel')}")

# Test lookup
cells = mapper.get_connected_cells('hru', 1)
print(f"HRU 1 connects to {len(cells)} cells: {cells}")
```

---

## Additional Resources

- **SWAT+ documentation:** https://swat.tamu.edu/media/69419/swatplus-io-documentation.pdf
- **MODFLOW 6 discretization:** https://pubs.usgs.gov/tm/06/a57/tm6a57.pdf
- **QGIS tutorials:** https://docs.qgis.org/

---

## File Checklist

Before running the coupling simulation, ensure:

- [x] Connections file created with all required attributes
- [x] CRS matches both SWAT+ and MODFLOW models
- [x] HRU fraction sums validated
- [x] Channel lengths calculated correctly
- [x] File path referenced in `CouplingSettings.geo_connections_file`
- [x] Test load successful with `GeographicMapper`

**You're now ready to run the coupled simulation!**