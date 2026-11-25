"""Detección de cambios abruptos (homogeneidad) con la prueba SNHT sobre la diferencia contra las vecinas.

Si una estación cambió de sensor o de sitio, su serie 'salta' respecto a sus vecinas.  Rellenar una serie así con
sus propios datos la empeora, por eso se marca y las estimaciones de esa estación/variable bajan a 'baja confianza'.

  * Temperatura: diferencia de medias mensuales contra el promedio de las vecinas (°C).
  * Precipitación: log-razón de totales mensuales contra el promedio de las vecinas (meses con >= 25 días válidos);
    se reporta como cambio relativo (%).
Es una heurística: el valor crítico es aproximado.
"""
import numpy as np
import pandas as pd

from .config import TEMPS, VARS

T_CRIT = 9.0      # aprox. al 95 % para ~60 valores mensuales (Khaliq & Ouarda, 2007)
MIN_JUMP = 0.8    # °C: un salto menor no se marca (evita falsos positivos en las vecinas de la estación que sí cambió)
MIN_REL = 0.25    # lluvia: un cambio relativo menor al 25 % no se marca


def snht(z):
    n = len(z)
    best, k_best = 0.0, None
    for k in range(6, n - 6):
        t = k * z[:k].mean() ** 2 + (n - k) * z[k:].mean() ** 2
        if t > best:
            best, k_best = t, k
    return best, k_best


def detect(diff, relative=False):
    """diff: serie mensual (estación - vecinas, o log-razón en lluvia). Devuelve (T, posición, salto, hay_cambio)."""
    z = ((diff - diff.mean()) / diff.std()).to_numpy()
    t, k = snht(z)
    jump = float(diff.iloc[k:].mean() - diff.iloc[:k].mean())
    big = (abs(np.expm1(jump)) >= MIN_REL) if relative else (abs(jump) >= MIN_JUMP)
    return t, k, jump, bool(t > T_CRIT and big)


def _monthly_diff(var, ds, j, ids):
    a = ds.diag_values[var] if ds.diag_values else ds.values[var]
    if var in TEMPS:
        return pd.Series(a[:, j] - np.nanmean(a[:, ids], axis=1), index=ds.dates).resample("MS").mean().dropna()
    own = pd.DataFrame({"x": a[:, j]}, index=ds.dates).resample("MS").agg(["sum", "count"])["x"]
    nb = pd.DataFrame(np.nanmean(a[:, ids], axis=1), index=ds.dates).resample("MS").agg(["sum", "count"])[0]
    ok = (own["count"] >= 25) & (nb["count"] >= 25)
    return np.log((own["sum"][ok] + 1.0) / (nb["sum"][ok] + 1.0)).dropna()


def homogeneity_table(ds, nbrs):
    rows = []
    for v in VARS:
        for j, st in enumerate(ds.stations.itertuples()):
            ids = nbrs[v].ids[j]
            if len(ids) < 1:
                continue
            diff = _monthly_diff(v, ds, j, ids)
            if len(diff) < 36 or diff.std() == 0:
                continue
            t, k, jump, hit = detect(diff, relative=v not in TEMPS)
            rows.append({"clave_estacion": st.clave_estacion, "variable": v, "estadistico_T": round(float(t), 2),
                         "critico_aprox": T_CRIT, "posible_cambio": hit,
                         "fecha_probable": diff.index[k].strftime("%Y-%m") if hit else "",
                         "salto_medio": round(jump if v in TEMPS else float(np.expm1(jump)) * 100, 2) if hit else 0.0,
                         "unidad_salto": "°C" if v in TEMPS else "%", "meses_usados": int(len(diff))})
    return pd.DataFrame(rows)
