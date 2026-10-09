# SWAT+ - MODFLOW 6 Coupling: GIS Setup Quick Reference

## Prerequisites Checklist

Before creating the connections file in QGIS, ensure you have:

- [ ] **SWAT+ model files**
  - [ ] HRU polygons shapefile (`hrus.shp`)
  - [ ] Channel/reach lines shapefile (`channels.shp`)
  - [ ] Subbasin polygons (optional, for reference)
  
- [ ] **MODFLOW 6 model files**
  - [ ] DIS file with grid dimensions (NROW, NCOL, NLAY)
  - [ ] Model extent/boundary polygon or grid shapefile
  
- [ ] **Software**
  - [ ] QGIS 3.28+ (LTR) installed
  - [ ] Python with GeoPandas (for validation)
  - [ ] Text editor for viewing CSV/attribute tables

---

## 5-Step QGIS Workflow

### Step 1: Load Layers
```
1. Open QGIS
2. Add Vector Layers:
   - HRU polygons
   - Channel lines
   - MODFLOW grid (or create from extent)
3. Verify CRS matches for all layers
```

### Step 2: Create MODFLOW Grid (if needed)
```
Vector → Research Tools → Create Grid
  - Grid type: Rectangle (polygon)
  - Grid extent: [Match MODFLOW model extent]
  - Horizontal spacing: [DELR value]
  - Vertical spacing: [DELC value]
  - Grid CRS: [Same as SWAT+ layers]
  
Add grid IDs:
  - Field Calculator: grid_id = $id
```

### Step 3: Intersect HRUs with Grid
```
Vector → Geoprocessing Tools → Intersection
  - Input layer: HRU polygons
  - Overlay layer: MODFLOW grid
  - Output: hru_grid_intersect.gpkg
  
Calculate fractions:
  - Open attribute table
  - Field Calculator:
    - Name: area_m2
    - Expression: $area
  - Join back to original HRUs to get total area
  - Calculate: fraction = area_m2 / total_hru_area
```

### Step 4: Intersect Channels with Grid
```
Vector → Geoprocessing Tools → Intersection
  - Input layer: Channel lines
  - Overlay layer: MODFLOW grid
  - Output: channel_grid_intersect.gpkg
  
Calculate lengths:
  - Field Calculator:
    - Name: length_m
    - Expression: $length
```

### Step 5: Build Connections Table
```
1. Merge HRU and channel intersections
2. Add required fields:
   - swat_obj_type (text)
   - swat_obj_id (integer)
   - mf_layer (integer, usually 1)
   - mf_row (integer, calculate from grid_id)
   - mf_col (integer, calculate from grid_id)
   - fraction (real, for HRUs)
   - length_m (real, for channels)
   
3. Populate values using Field Calculator
4. Export as connections.gpkg
```

---

## Field Calculator Formulas

### Calculate MODFLOW Row/Col from Grid ID
```python
# Assuming grid starts at top-left, numbered left-to-right, top-to-bottom
NCOL = 100  # Replace with your column count

# Row number (1-based)
mf_row = floor("grid_id" / NCOL) + 1

# Column number (1-based)
mf_col = ("grid_id" % NCOL) + 1
```

### Calculate HRU Area Fraction
```python
# First, add total HRU area to intersect layer via join
# Then:
fraction = "intersect_area" / "total_hru_area"
```

### Set Object Type
```python
# For HRU features
swat_obj_type = 'hru'

# For channel features
swat_obj_type = 'channel'
```

---

## Automated Script Option

Instead of manual steps, run the QGIS Python script:

```
1. Open QGIS Python Console (Ctrl+Alt+P)
2. Click "Show Editor"
3. Load: examples/qgis_create_connections.py
4. Edit configuration section (layer names, dimensions)
5. Run Script (green play button)
```

---

## Validation

After creating connections.gpkg, validate:

```bash
# Windows
.venv\Scripts\python examples/validate_connections.py connections.gpkg --modflow-grid 50 100 3

# Linux/Mac
source .venv/bin/activate
python3 examples/validate_connections.py connections.gpkg --modflow-grid 50 100 3
```

**Expected output:**
- ✓ All required attributes present
- ✓ Data types correct
- ✓ HRU fraction sums ≈ 1.0 (within 5%)
- ✓ MODFLOW indices within grid bounds
- ✓ No duplicate connections

---

## Common Issues

| Issue | Solution |
|-------|----------|
| CRS mismatch | Reproject all layers to same CRS before intersection |
| Gaps in HRU coverage | Buffer HRUs by 0.1m before intersection |
| Fraction sums ≠ 1.0 | Check for overlaps/gaps in HRU layer; normalize fractions |
| Grid IDs not sequential | Recalculate grid_id using: `@row_number - 1` |
| Channels not intersecting | Verify channel layer overlaps grid extent |

---

## Example Connections Data

Sample of final connections table:

| swat_obj_type | swat_obj_id | mf_layer | mf_row | mf_col | fraction | length_m |
|---------------|-------------|----------|--------|--------|----------|----------|
| hru           | 1           | 1        | 10     | 15     | 0.65     |          |
| hru           | 1           | 1        | 10     | 16     | 0.35     |          |
| channel       | 101         | 1        | 10     | 15     |          | 450.0    |
| channel       | 101         | 1        | 10     | 16     |          | 550.0    |

---

## Next Steps

After creating and validating connections file:

1. **Place file in project directory**
   ```
   project/
   ├── models/
   │   ├── swat_model/
   │   ├── modflow_model/
   │   └── connections.gpkg  ← Place here
   ```

2. **Update coupling configuration**
   ```python
   settings = CouplingSettings(
       modflow_dir="models/modflow_model",
       swatplus_dir="models/swat_model",
       geo_connections_file="models/connections.gpkg",  # ← Reference here
       ...
   )
   ```

3. **Run coupling simulation**
   ```bash
   python examples/run_coupling_example.py
   ```

---

## Full Documentation

**Detailed guide:** [docs/GIS_PREPROCESSING.md](GIS_PREPROCESSING.md)

**Questions?** See [docs/ARCHITECTURE.md](ARCHITECTURE.md) for technical details
