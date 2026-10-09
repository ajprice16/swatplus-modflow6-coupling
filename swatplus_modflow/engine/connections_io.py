"""Readers and validators for the connection tables of docs/DATA_CONTRACTS.md.

* ``hru_cells.csv``      (hru_id, layer, row, col, area_m2, fraction)
* ``sfr_reaches.csv``    (rno, channel_id, seq, layer, row, col, length_m)
* ``well_cells.csv``     (layer, row, col, rate_m3d)   -- optional pumping table

Indices are 1-based in the files; the arrays handed to the driver are 0-based.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from .errors import EngineInputError

log = logging.getLogger(__name__)

AREA_MISMATCH_PERCENT = 5.0
FRACTION_SUM_TOL = 1.0e-3


@dataclass
class HruCells:
    hru_id: np.ndarray        # int64 (n,)
    layer: np.ndarray         # 0-based
    row: np.ndarray
    col: np.ndarray
    fraction: np.ndarray
    area_m2: np.ndarray       # polygon area inside the cell


@dataclass
class SfrReaches:
    rno: np.ndarray           # 0-based reach index, contiguous 0..n-1, sorted
    channel_id: np.ndarray
    seq: np.ndarray
    layer: np.ndarray         # 0-based
    row: np.ndarray
    col: np.ndarray
    length_m: np.ndarray

    @property
    def n(self) -> int:
        return len(self.rno)

    def reaches_by_channel(self) -> Dict[int, List[Tuple[int, float]]]:
        """``{channel_id: [(rno0, share of the channel's length), ...]}``; shares sum to 1."""
        out: Dict[int, List[Tuple[int, float]]] = {}
        for cid in np.unique(self.channel_id):
            m = self.channel_id == cid
            tot = float(self.length_m[m].sum())
            if tot <= 0:
                raise EngineInputError(f"sfr_reaches: channel {cid} has zero total length_m")
            out[int(cid)] = [(int(r), float(l) / tot) for r, l in zip(self.rno[m], self.length_m[m])]
        return out


def _need(df: pd.DataFrame, cols, name: str) -> None:
    miss = [c for c in cols if c not in df.columns]
    if miss:
        raise EngineInputError(f"{name}: missing column(s) {miss}; found {list(df.columns)}")


def _check_cells(layer, row, col, dims, name: str) -> None:
    nlay, nrow, ncol = dims
    for arr, lo_hi, label in ((layer, nlay, "layer"), (row, nrow, "row"), (col, ncol, "col")):
        bad = (arr < 1) | (arr > lo_hi)
        if bad.any():
            raise EngineInputError(
                f"{name}: {int(bad.sum())} row(s) have {label} outside 1..{lo_hi} "
                f"(first value {int(arr[bad][0])}); indices are 1-based")


def read_hru_cells(path: Path, dims: Tuple[int, int, int] | None = None) -> HruCells:
    df = pd.read_csv(path)
    _need(df, ["hru_id", "layer", "row", "col", "area_m2", "fraction"], Path(path).name)
    if df.empty:
        raise EngineInputError(f"{Path(path).name}: no rows")
    if df[["hru_id", "layer", "row", "col", "area_m2", "fraction"]].isna().any().any():
        raise EngineInputError(f"{Path(path).name}: empty cells in required columns")
    if (df["fraction"] < 0).any() or (df["area_m2"] < 0).any():
        raise EngineInputError(f"{Path(path).name}: negative fraction or area_m2")
    if dims is not None:
        _check_cells(df["layer"].to_numpy(), df["row"].to_numpy(), df["col"].to_numpy(), dims, Path(path).name)
    return HruCells(
        hru_id=df["hru_id"].to_numpy(np.int64), layer=df["layer"].to_numpy(np.int64) - 1,
        row=df["row"].to_numpy(np.int64) - 1, col=df["col"].to_numpy(np.int64) - 1,
        fraction=df["fraction"].to_numpy(np.float64), area_m2=df["area_m2"].to_numpy(np.float64))


def hru_cell_diagnostics(hc: HruCells, hru_con_area_m2: Dict[int, float]) -> Tuple[List[str], List[str]]:
    """Return ``(errors, warnings)`` for an HRU table checked against hru.con areas."""
    errors, warnings = [], []
    ids = np.unique(hc.hru_id)
    missing = [int(i) for i in ids if int(i) not in hru_con_area_m2]
    if missing:
        errors.append(f"hru_cells: {len(missing)} hru_id value(s) are not in hru.con (first: {missing[:5]})")
    df = pd.DataFrame({"hru_id": hc.hru_id, "fraction": hc.fraction, "area": hc.area_m2})
    g = df.groupby("hru_id").agg(fsum=("fraction", "sum"), asum=("area", "sum"))
    over = g[g["fsum"] > 1.0 + FRACTION_SUM_TOL]
    if len(over):
        errors.append(f"hru_cells: fractions of {len(over)} HRU(s) sum to more than 1 (first: "
                      f"{int(over.index[0])} = {over['fsum'].iloc[0]:.4f}); that would create water")
    under = g[g["fsum"] < 1.0 - FRACTION_SUM_TOL]
    if len(under):
        lost = sum((1.0 - r.fsum) * hru_con_area_m2.get(int(i), 0.0) for i, r in under.iterrows())
        warnings.append(
            f"hru_cells: {len(under)} HRU(s) have fractions summing to less than 1 (partly outside the "
            f"grid); their percolation over about {lost:,.0f} m2 of hru.con area leaves the model")
    mism = []
    for i, r in g.iterrows():
        a_con = hru_con_area_m2.get(int(i))
        # whole-polygon area = area inside the grid / fraction inside the grid, so an HRU that is
        # partly outside the grid is not reported twice (it is already reported above)
        a_poly = r.asum / r.fsum if r.fsum > 0 else 0.0
        if a_con and a_con > 0 and a_poly > 0:
            if abs(a_poly - a_con) / a_con * 100.0 > AREA_MISMATCH_PERCENT:
                mism.append((int(i), a_poly, a_con))
    if mism:
        i, a_poly, a_con = mism[0]
        warnings.append(
            f"hru_cells: polygon area differs from hru.con area by more than {AREA_MISMATCH_PERCENT:g}% "
            f"for {len(mism)} HRU(s) (first: HRU {i}, polygon {a_poly:,.0f} m2 vs hru.con {a_con:,.0f} m2). "
            "Percolation volumes use the hru.con area")
    no_perc = [i for i in hru_con_area_m2 if i not in set(ids.tolist())]
    if no_perc:
        warnings.append(f"hru_cells: {len(no_perc)} hru.con HRU(s) have no cell rows; their percolation "
                        "is not applied to the aquifer")
    return errors, warnings


def read_sfr_reaches(path: Path, dims: Tuple[int, int, int] | None = None) -> SfrReaches:
    df = pd.read_csv(path)
    _need(df, ["rno", "channel_id", "seq", "layer", "row", "col", "length_m"], Path(path).name)
    if df.empty:
        raise EngineInputError(f"{Path(path).name}: no rows")
    df = df.sort_values("rno").reset_index(drop=True)
    rno = df["rno"].to_numpy(np.int64)
    if rno.min() == 1:
        rno = rno - 1
    elif rno.min() == 0:
        log.warning("%s: rno starts at 0; treating reach numbers as 0-based. The MODFLOW builder writes "
                    "1-based numbers, as in the SFR input file", Path(path).name)
    else:
        raise EngineInputError(f"{Path(path).name}: rno must start at 1 (or 0), starts at {rno.min()}")
    if not np.array_equal(rno, np.arange(len(rno))):
        raise EngineInputError(f"{Path(path).name}: rno must be contiguous with no duplicates")
    if (df["length_m"] <= 0).any():
        raise EngineInputError(f"{Path(path).name}: length_m must be positive")
    if dims is not None:
        _check_cells(df["layer"].to_numpy(), df["row"].to_numpy(), df["col"].to_numpy(), dims, Path(path).name)
    return SfrReaches(
        rno=rno, channel_id=df["channel_id"].to_numpy(np.int64), seq=df["seq"].to_numpy(np.int64),
        layer=df["layer"].to_numpy(np.int64) - 1, row=df["row"].to_numpy(np.int64) - 1,
        col=df["col"].to_numpy(np.int64) - 1, length_m=df["length_m"].to_numpy(np.float64))


@dataclass
class WellCells:
    layer: np.ndarray
    row: np.ndarray
    col: np.ndarray
    rate_m3d: np.ndarray


def read_well_cells(path: Path, dims: Tuple[int, int, int] | None = None) -> WellCells:
    """Wells: one row per well cell, ``rate_m3d`` in MODFLOW sign (negative = extraction)."""
    df = pd.read_csv(path)
    _need(df, ["layer", "row", "col", "rate_m3d"], Path(path).name)
    if df[["layer", "row", "col", "rate_m3d"]].isna().any().any():
        raise EngineInputError(f"{Path(path).name}: empty cells in required columns")
    if dims is not None:
        _check_cells(df["layer"].to_numpy(), df["row"].to_numpy(), df["col"].to_numpy(), dims, Path(path).name)
    return WellCells(df["layer"].to_numpy(np.int64) - 1, df["row"].to_numpy(np.int64) - 1,
                     df["col"].to_numpy(np.int64) - 1, df["rate_m3d"].to_numpy(np.float64))
