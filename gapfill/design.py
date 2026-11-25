"""Matriz de entrada de los modelos a partir de un FeatureSet.

Temperatura: el modelo aprende la ANOMALÍA (valor - normal climática de la estación) y las demás variables
también se expresan como anomalías.  Los árboles no extrapolan; trabajar con anomalías evita que el modelo
tenga que memorizar el ciclo estacional.
Lluvia: se usan los valores tal cual; el objetivo se transforma con log1p dentro de cada modelo.
"""
import numpy as np

from .config import TEMPS
from .features import FEATURES

_ANOM = ("est_vecinos", "est_local", "idw", "pond_corr", "lineal", "prev_val", "next_val")


def design(var, fs):
    if var in TEMPS:
        # 'frac_lluvia' solo existe para lluvia (en temperatura está vacía y rompe el binning de boosting)
        X = np.column_stack([fs[k] for k in FEATURES if k != "frac_lluvia"])
    else:   # el desfase local solo se usa en temperatura
        X = np.column_stack([fs[k] for k in FEATURES if k not in ("off_local", "est_local")])
    if var in TEMPS:
        base = fs["clima_propio"]
        extra = np.column_stack([fs[k] - base for k in _ANOM])
        return np.column_stack([X, extra]), base
    return X, np.zeros(len(fs))
