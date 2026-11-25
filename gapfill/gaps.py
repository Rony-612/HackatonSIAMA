"""Huecos: longitud, clasificación (corto/medio/largo) y diagnóstico detallado."""
import json

import numpy as np
import pandas as pd


def gap_lengths(isnan: np.ndarray) -> np.ndarray:
    """Para cada celda faltante, la longitud de la racha de días consecutivos faltantes a la que pertenece.
    Las celdas con dato valen 0.  Entrada y salida: matriz (días, estaciones)."""
    out = np.zeros(isnan.shape, dtype=int)
    for j in range(isnan.shape[1]):
        edges = np.flatnonzero(np.diff(np.r_[False, isnan[:, j], False].astype(np.int8)))
        for start, end in edges.reshape(-1, 2):
            out[start:end, j] = end - start
    return out


def gap_type(length):
    """Clasificación del reto: corto 1-3 días, medio 4-30, largo > 30."""
    length = np.asarray(length)
    return np.where(length <= 3, "corto", np.where(length <= 30, "medio", "largo"))


def gap_bucket(length):
    """Versión más fina de 'largo', para ver cómo se degrada el modelo en huecos muy largos."""
    length = np.asarray(length)
    return np.select([length <= 3, length <= 30, length <= 60], ["1-3", "4-30", "31-60"], "61+")


def diagnose(ds, var: str):
    """Diagnóstico por estación: % de huecos, cuántos de cada tipo, dónde caen en el tiempo y lista de huecos."""
    base = ds.diag_values[var] if ds.diag_values else ds.values[var]   # sin esconder los objetivos del test público
    miss = np.isnan(base) & ds.active
    rawmiss = ds.raw_missing[var] & ds.active if ds.raw_missing else miss
    summary, gaps = [], []
    for j, st in enumerate(ds.stations.itertuples()):
        m = miss[:, j]
        edges = np.flatnonzero(np.diff(np.r_[False, m, False].astype(np.int8))).reshape(-1, 2)
        lens = edges[:, 1] - edges[:, 0]
        for (s, e), n in zip(edges, lens):
            gaps.append({"clave_estacion": st.clave_estacion, "variable": var,
                         "inicio": ds.dates[s].strftime("%Y-%m-%d"), "fin": ds.dates[e - 1].strftime("%Y-%m-%d"),
                         "longitud": int(n), "tipo": str(gap_type(n))})
        s_m = pd.Series(m, index=ds.dates)
        by_year = s_m.groupby(s_m.index.year).sum().astype(int).to_dict()
        by_month = s_m.groupby(s_m.index.month).sum().astype(int).to_dict()
        expected = int(ds.active[:, j].sum())
        summary.append({
            "clave_estacion": st.clave_estacion, "variable": var, "esperados": expected, "faltantes": int(m.sum()),
            "porcentaje_vacios": round(100 * m.sum() / max(1, expected), 2),
            "porcentaje_vacios_crudo": round(100 * rawmiss[:, j].sum() / max(1, expected), 2),
            "cortos": int((lens <= 3).sum()), "medios": int(((lens > 3) & (lens <= 30)).sum()),
            "largos": int((lens > 30).sum()), "hueco_mas_largo": int(lens.max()) if len(lens) else 0,
            "invalidos_o_descartados_qc": int((ds.invalid[var][:, j] & ds.active[:, j]).sum()), "sospechosos": int(ds.suspicious[var][:, j].sum()),
            "faltantes_por_anio": json.dumps({str(k): v for k, v in by_year.items()}),
            "faltantes_por_mes": json.dumps({str(k): v for k, v in by_month.items()}),
        })
    return summary, gaps
