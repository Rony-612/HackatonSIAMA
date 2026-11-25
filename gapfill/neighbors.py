"""Climatología, anomalías y selección de vecinos por CORRELACIÓN (no solo por cercanía)."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import TEMPS, Config


def haversine(lat, lon):
    lat, lon = np.radians(lat), np.radians(lon)
    h = np.sin((lat[:, None] - lat) / 2) ** 2 + np.cos(lat[:, None]) * np.cos(lat) * np.sin((lon[:, None] - lon) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))


def _circular_sum(x, w):
    ext = np.concatenate([x[-w:], x, x[:w]], axis=0)
    c = np.cumsum(np.vstack([np.zeros((1, x.shape[1])), ext]), axis=0)
    return c[2 * w + 1:] - c[:-(2 * w + 1)]


def climatology(a, doy, window_mask, half_window):
    """Promedio por día del año de cada estación, usando SOLO los días de window_mask, suavizado en círculo.
    Devuelve matriz (días, estaciones): la 'normal' esperada para cada fecha."""
    S = a.shape[1]
    doy = np.minimum(doy, 365) - 1          # el 29 de febrero comparte día con el 28 (0..364)
    sums, cnt = np.zeros((365, S)), np.zeros((365, S))
    x, d = a[window_mask], doy[window_mask]
    ok = np.isfinite(x)
    np.add.at(sums, d, np.where(ok, x, 0.0))
    np.add.at(cnt, d, ok.astype(float))
    s, c = _circular_sum(sums, half_window), _circular_sum(cnt, half_window)
    with np.errstate(invalid="ignore", divide="ignore"):
        clim = np.where(c > 0, s / c, np.nan)
    station_mean = np.nanmean(a[window_mask], axis=0)
    station_mean = np.where(np.isfinite(station_mean), station_mean, np.nanmean(station_mean))
    clim = np.where(np.isfinite(clim), clim, station_mean[None, :])
    return clim[doy]


@dataclass
class NeighborTable:
    ids: list          # ids[j] = índices de las vecinas de la estación j (ordenadas de mejor a peor)
    dist: np.ndarray   # distancias (km)
    corr: np.ndarray   # correlaciones


def select_neighbors(var, stations, a, clim, window_mask, cfg: Config, basin=None) -> NeighborTable:
    """Vecinas = dentro del radio Y correlacionadas (con datos del periodo de entrenamiento únicamente).
    Temperatura: correlación de ANOMALÍAS (quita la estacionalidad compartida, que infla Pearson).
    Lluvia: Spearman sobre los valores (robusta a la cola pesada)."""
    dist = haversine(stations.latitud.to_numpy(float), stations.longitud.to_numpy(float))
    train = a[window_mask]
    if var in TEMPS:
        corr = pd.DataFrame(train - clim[window_mask]).corr(min_periods=cfg.min_overlap).to_numpy()
    else:
        corr = pd.DataFrame(train).corr(method="spearman", min_periods=cfg.min_overlap).to_numpy()
    alt = stations.altitud.to_numpy(float)
    thr = cfg.min_corr[var]
    ids = []
    for j in range(len(stations)):
        cand = [k for k in range(len(stations)) if k != j and dist[j, k] <= cfg.radius_km
                and np.isfinite(corr[j, k]) and corr[j, k] >= thr]
        def score(k):
            s = corr[j, k] / (1 + dist[j, k] / cfg.radius_km + abs(alt[j] - alt[k]) / 500)
            if basin is not None and basin[j] >= 0 and basin[k] >= 0 and basin[j] != basin[k]:
                s *= cfg.same_basin_penalty   # otra cuenca/ladera de la sierra: clima potencialmente distinto
            return s
        ids.append(np.array(sorted(cand, key=score, reverse=True)[:cfg.max_neighbors], dtype=int))
    return NeighborTable(ids, dist, corr)
