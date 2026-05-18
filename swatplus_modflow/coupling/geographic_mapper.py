"""Geographic mapping between SWAT+ and MODFLOW identifiers."""

import csv
from pathlib import Path
from typing import Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)


class GeographicMapper:
    """
    Maps connections between SWAT+ objects and MODFLOW grid cells.
    
    Handles:
    - HRU ↔ MODFLOW cell spatial intersections
    - Channel segment ↔ MODFLOW cell intersections
    - Reservoir ↔ MODFLOW cell connections
    - Canal ↔ MODFLOW cell intersections
    - Tile drain ↔ MODFLOW cell connections
    """
    
    def __init__(
        self,
        connections_file: Path,
        modflow_grid: Dict,
        swatplus_objects: Dict
    ):
        """
        Initialize geographic mapper.
        
        Args:
            connections_file: GIS file with connection information (shapefile/geopackage)
            modflow_grid: MODFLOW grid information (nrow, ncol, cell_size, etc)
            swatplus_objects: SWAT+ object information (HRU count, channel count, etc)
        """
        self.connections_file = Path(connections_file)
        self.modflow_grid = modflow_grid
        self.swatplus_objects = swatplus_objects
        
        # Connection mappings
        self.hru_to_cells: Dict[int, List[Tuple[int, float]]] = {}  # HRU -> [(cell, fraction)]
        self.channel_to_cells: Dict[int, List[Tuple[int, float]]] = {}  # Channel -> [(cell, length)]
        self.reservoir_to_cells: Dict[int, List[int]] = {}  # Reservoir -> [cells]
        self.canal_to_cells: Dict[int, List[Tuple[int, float]]] = {}  # Canal -> [(cell, length)]
        self.drain_to_cells: Dict[int, int] = {}  # Drain -> cell
        
        self.n_hru_connections = 0
        self.n_channel_connections = 0
        self.n_drain_connections = 0
        
        logger.info("Initialized GeographicMapper")
        logger.info("  Connections file: %s", self.connections_file)
    
    def load_connections(self) -> None:
        """
        Load geographic connections from GIS file.
        
        Reads a GIS layer with the following attributes:
        - swat_obj_type: 'HRU', 'Channel', 'Reservoir', 'Canal', 'Drain'
        - swat_obj_id: ID in SWAT+ model
        - mf_layer: MODFLOW layer
        - mf_row: MODFLOW row
        - mf_col: MODFLOW column
        - fraction/length: Spatial overlap fraction or length in cell
        """
        logger.info("Loading geographic connections from %s", self.connections_file)

        if self.connections_file.suffix.lower() == ".csv":
            self._load_csv_connections()
        else:
            self._load_gis_connections()

        logger.info("  Loaded %s HRU connections", self.n_hru_connections)
        logger.info("  Loaded %s channel connections", self.n_channel_connections)
        logger.info("  Loaded %s drain connections", self.n_drain_connections)

    def _load_csv_connections(self) -> None:
        """Load Verde-style or generic connection CSV files."""
        with self.connections_file.open(newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            return

        columns = set(rows[0])
        if {"hru_id", "mf_row", "mf_col"}.issubset(columns):
            self._load_hru_csv_rows(rows)
        elif {"channel_id", "mf_row", "mf_col"}.issubset(columns):
            self._load_channel_csv_rows(rows)
        elif {"swat_obj_type", "swat_obj_id", "mf_row", "mf_col"}.issubset(columns):
            for row in rows:
                self._add_generic_row(row)
        else:
            raise ValueError(
                f"Unsupported connection CSV schema in {self.connections_file}: "
                f"{', '.join(sorted(columns))}"
            )

    def _load_gis_connections(self) -> None:
        """Load generic connection records from a GIS layer."""
        try:
            import geopandas as gpd
        except ImportError as exc:
            raise ImportError("geopandas is required to read GIS connection files") from exc

        gdf = gpd.read_file(self.connections_file)
        for _, row in gdf.iterrows():
            self._add_generic_row(row)

    def _load_hru_csv_rows(self, rows) -> None:
        """Load `hru_cell_connections.csv` produced by the Verde workflow."""
        for row in rows:
            hru_id = int(row["hru_id"])
            mf_cell = self._cell_id_from_row(
                row.get("mf_layer", 1),
                row["mf_row"],
                row["mf_col"],
            )
            fraction = float(row.get("frac_of_hru") or row.get("fraction") or 1.0)
            self.hru_to_cells.setdefault(hru_id, []).append((mf_cell, fraction))
            self.n_hru_connections += 1

    def _load_channel_csv_rows(self, rows) -> None:
        """Load `riv_channel_connections.csv` produced by the Verde workflow."""
        for row in rows:
            channel_id = int(row["channel_id"])
            mf_cell = self._cell_id_from_row(
                row.get("mf_layer", 1),
                row["mf_row"],
                row["mf_col"],
            )
            length = float(row.get("chan_length_m") or row.get("length_m") or row.get("length") or 1.0)
            self.channel_to_cells.setdefault(channel_id, []).append((mf_cell, length))
            self.n_channel_connections += 1

    def _add_generic_row(self, row) -> None:
        """Add one generic schema row to the appropriate mapping."""
        obj_type = str(row.get("swat_obj_type", "")).upper()
        obj_id = int(row.get("swat_obj_id"))
        mf_cell = self._cell_id_from_row(
            row.get("mf_layer", 1),
            row.get("mf_row"),
            row.get("mf_col"),
        )

        if obj_type == "HRU":
            fraction = float(row.get("fraction", 1.0))
            self.hru_to_cells.setdefault(obj_id, []).append((mf_cell, fraction))
            self.n_hru_connections += 1
        elif obj_type == "CHANNEL":
            length = float(row.get("length_m") or row.get("length") or 500.0)
            self.channel_to_cells.setdefault(obj_id, []).append((mf_cell, length))
            self.n_channel_connections += 1
        elif obj_type == "RESERVOIR":
            self.reservoir_to_cells.setdefault(obj_id, []).append(mf_cell)
        elif obj_type == "CANAL":
            length = float(row.get("length_m") or row.get("length") or 500.0)
            self.canal_to_cells.setdefault(obj_id, []).append((mf_cell, length))
        elif obj_type == "DRAIN":
            self.drain_to_cells[obj_id] = mf_cell
            self.n_drain_connections += 1
    
    def _cell_id_from_row(self, layer: int, row: int, col: int) -> int:
        """Convert layer/row/col to a zero-based linear cell ID."""
        ncol = int(self.modflow_grid.get('ncol', 1))
        nrow = int(self.modflow_grid.get('nrow', 1))
        index_base = int(self.modflow_grid.get("index_base", 0))
        layer_idx = int(layer) - 1
        row_idx = int(row) - index_base
        col_idx = int(col) - index_base
        return layer_idx * nrow * ncol + row_idx * ncol + col_idx
    
    def get_connected_cells(self, obj_type: str, obj_id: int) -> List[int]:
        """
        Get MODFLOW cells connected to a SWAT+ object.
        
        Args:
            obj_type: 'HRU', 'Channel', 'Reservoir', 'Canal', or 'Drain'
            obj_id: SWAT+ object ID
        
        Returns:
            List of connected cell IDs
        """
        mapping = {
            'HRU': self.hru_to_cells,
            'CHANNEL': self.channel_to_cells,
            'RESERVOIR': self.reservoir_to_cells,
            'CANAL': self.canal_to_cells,
            'DRAIN': self.drain_to_cells,
        }
        
        connections = mapping.get(obj_type.upper())
        if connections is None:
            return []
        
        obj_connections = connections.get(obj_id, [])
        
        # Extract cell IDs (first element of tuple if tuple, otherwise direct ID)
        cells = []
        for conn in obj_connections:
            if isinstance(conn, tuple):
                cells.append(conn[0])
            else:
                cells.append(conn)
        
        return cells
