"""Métricas del reto: MAE, RMSE, sesgo, KGE, POD/FAR (lluvia) y cobertura del intervalo."""
import numpy as np

from .config import RAIN_MM


def score(var, y, p, lo=None, hi=None, total=None):
    ok = np.isfinite(p) & np.isfinite(y)
    total = len(y) if total is None else total
    y, p = y[ok], p[ok]
    n = len(y)
    r = {"n": int(n), "total": int(total), "fraccion_estimada": n / total if total else 0.0}
    if n == 0:
        return r
    err = p - y
    r.update(mae=float(np.abs(err).mean()), rmse=float(np.sqrt((err ** 2).mean())), sesgo=float(err.mean()), kge=None)
    if n > 2 and y.std() > 0 and p.std() > 0 and abs(y.mean()) > 1e-8:
        r["kge"] = float(1 - np.sqrt((np.corrcoef(y, p)[0, 1] - 1) ** 2 + (p.std() / y.std() - 1) ** 2
                                     + (p.mean() / y.mean() - 1) ** 2))
    if lo is not None:
        lo, hi = lo[ok], hi[ok]
        r.update(cobertura=float(((y >= lo) & (y <= hi)).mean()), ancho_intervalo=float((hi - lo).mean()))
    if var == "precipitacion":
        a, b = y >= RAIN_MM, p >= RAIN_MM
        r["pod"] = float((a & b).sum() / a.sum()) if a.any() else None
        r["far"] = float((~a & b).sum() / b.sum()) if b.any() else None
    return r


def by_group(var, y, p, lo, hi, groups, total_by_group=None):
    out = {}
    for g in np.unique(groups):
        m = groups == g
        out[str(g)] = score(var, y[m], p[m], lo[m], hi[m], total=int(m.sum()))
    return out
