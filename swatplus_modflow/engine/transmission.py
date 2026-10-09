"""Lane-style transmission loss for ephemeral-channel local inflow.

The equations are those of the USDA NRCS National Engineering Handbook, Part 630,
Chapter 19 (transmission losses), as transcribed in the research implementation
this module was generalised from (handbook units: reach length in miles, width in
feet, volumes in acre-feet, K in in/h, duration D in hours):

    a(D)   = -0.00465 K D                       unit-channel intercept
    k(D,P) = -1.09 ln[1 - 0.00545 K D / P]      unit-channel decay factor
    b      = exp(-k);  b(x,w) = exp(-k x w);  a(x,w) = a/(1-b) [1 - b(x,w)]
    Q      = a(x,w) + b(x,w) P + Q_L/(k w) [1 - b(x,w)]    (lateral inflow Q_L per mile)

Choices made here (judgment calls, not handbook prescriptions):

* Applied to a channel's LOCAL inflow only (flo_out minus upstream flo_out), as a
  uniform lateral inflow with P = 0. Upstream flow is routed by the MODFLOW SFR
  package, whose streambed already exchanges water with the aquifer.
* With P = 0 the decay factor uses the total local volume as P; when
  0.00545 K D / V >= 1 the whole volume is lost.
* The caller decides which channels are "losing" (see the driver: stream-bed
  bottom above the initial head in most reaches).
* D is not resolved by a daily model, so it is a project parameter.

The loss removed from SFR runoff is returned to the caller, which adds it to
recharge in the reach cells, so water is moved, not created or destroyed.
The implementation has not been re-verified against the handbook's worked
examples in this repository; do that before relying on absolute loss values.
"""
from __future__ import annotations

import numpy as np

M3_PER_ACFT = 1233.48184
FT_PER_M = 1.0 / 0.3048
MI_PER_KM = 1.0 / 1.609344


def lane_outflow_acft(P, QL_total, x_mi, w_ft, K_inh, D_h, P_for_k):
    """Outflow volume (acre-ft); returns ``(Q, a_xw, b_xw, k)``. ``0 <= Q <= P + QL_total``."""
    P, QLt, x, w, Pk = np.broadcast_arrays(
        *(np.asarray(v, dtype=float) for v in (P, QL_total, x_mi, w_ft, P_for_k)))
    tot = P + QLt
    arg = np.where(Pk > 0, 1.0 - 0.00545 * K_inh * D_h / np.where(Pk > 0, Pk, 1.0), -1.0)
    ok = (tot > 0) & (arg > 0)
    k = np.where(ok, -1.09 * np.log(np.where(ok, arg, 1.0)), np.nan)
    a = -0.00465 * K_inh * D_h
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        b = np.exp(-k)
        bxw = np.exp(-k * x * w)
        axw = a / (1 - b) * (1 - bxw)
        lat = np.where(QLt > 0, (QLt / x) / (k * w) * (1 - bxw), 0.0)
        Q = axw + bxw * P + lat
    Q = np.where(ok, np.clip(Q, 0.0, tot), 0.0)
    return Q, axw, bxw, k


def lane_outflow_lateral(V_m3, len_km, wd_m, K_inh_per_h: float, D_h: float) -> np.ndarray:
    """Outflow (m3) of local inflow ``V_m3`` entering uniformly along a reach, no upstream inflow."""
    V = np.asarray(V_m3, dtype=float) / M3_PER_ACFT
    Q, *_ = lane_outflow_acft(0.0, V, np.asarray(len_km, float) * MI_PER_KM,
                              np.asarray(wd_m, float) * FT_PER_M, K_inh_per_h, D_h, V)
    return Q * M3_PER_ACFT


MM_PER_H_TO_IN_PER_H = 1.0 / 25.4
