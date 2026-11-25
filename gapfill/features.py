"""Variables (features) para estimar un día faltante de la estación j.

Tres familias:
  1. contexto: estacionalidad, altitud, coordenadas, normal climática de la propia estación;
  2. propia serie: valor antes/después del hueco, días hasta cada uno, interpolación lineal, longitud del hueco;
  3. vecinas: promedios ponderados (IDW y por correlación), transferencia de anomalía (temperatura) o razón normal
     (lluvia), cuántas vecinas observadas hay ese día, distancia/altitud/correlación medias.
Se construyen sobre una matriz que YA tiene los huecos (naturales + artificiales), así que no filtran la respuesta.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import RAIN_MM, RAIN_MONTHS, TEMPS
from .gaps import gap_lengths, gap_type

FEATURES = ["sin_doy", "cos_doy", "altitud", "latitud", "longitud", "clima_propio",
            "prev_val", "next_val", "lineal", "dias_prev", "dias_next", "long_hueco",
            "n_vecinos", "idw", "pond_corr", "est_vecinos", "dist_media", "dalt_media",
            "corr_media", "frac_lluvia", "max_vecinos", "suma_corr", "dist_min", "frac_misma_cuenca", "off_local", "est_local"]


@dataclass
class Ctx:
    var: str
    sin: np.ndarray
    cos: np.ndarray
    alt: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    clim: np.ndarray       # (días, estaciones): normal esperada de cada fecha
    months: np.ndarray
    basin: np.ndarray = None


def make_ctx(var, ds, clim):
    doy = ds.dates.dayofyear.to_numpy()
    return Ctx(var, np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25),
               ds.stations.altitud.to_numpy(float), ds.stations.latitud.to_numpy(float),
               ds.stations.longitud.to_numpy(float), clim, ds.dates.month.to_numpy(), ds.basin)


def _own(y):
    """Features de la propia serie alrededor de cada día (los días con dato se usan solo para armar los vecinos temporales)."""
    T = len(y)
    idx = np.arange(T)
    fin = np.isfinite(y)
    prev = np.maximum.accumulate(np.where(fin, idx, -1))
    nxt = np.minimum.accumulate(np.where(fin, idx, T)[::-1])[::-1]
    hp, hn = prev >= 0, nxt < T
    pv = np.where(hp, y[np.clip(prev, 0, T - 1)], np.nan)
    nv = np.where(hn, y[np.clip(nxt, 0, T - 1)], np.nan)
    lin = np.full(T, np.nan)
    both = hp & hn & ~fin
    span = np.maximum(nxt - prev, 1)
    lin[both] = pv[both] + (nv[both] - pv[both]) * (idx[both] - prev[both]) / span[both]
    return pv, nv, np.where(hp, idx - prev, 400).astype(float), np.where(hn, nxt - idx, 400).astype(float), lin


OFFSET_WINDOW = 90   # días a cada lado del hueco para estimar cuánto se aparta la estación de sus vecinas


def _local_offset(a_j, nb_mean, prev_idx, next_idx, window=OFFSET_WINDOW):
    """Diferencia (estación - vecinas) estimada con los ~90 días observados ANTES y DESPUÉS del hueco, interpolada
    linealmente a través de él. Corrige el desfase que cambia con los años entre dos estaciones (sensor, sitio, urbanización):
    con datos reales de Salamanca/La Joyita la diferencia en Tmin pasó de -2 a +3 °C entre los años 80 y hoy, y una anomalía
    calculada con el periodo de entrenamiento no lo puede ver. Usa solo valores observados de ambas estaciones."""
    d = pd.Series(a_j - nb_mean)
    pre = d.rolling(window, min_periods=10).mean().to_numpy()
    post = d[::-1].rolling(window, min_periods=10).mean().to_numpy()[::-1]
    T = len(a_j)
    idx = np.arange(T)
    pi, ni = idx - prev_idx, idx + next_idx            # posición del último dato antes y del primero después
    ok_p, ok_n = (prev_idx < 400) & (pi >= 0), (next_idx < 400) & (ni < T)
    p_v = np.where(ok_p, pre[np.clip(pi, 0, T - 1).astype(int)], np.nan)
    n_v = np.where(ok_n, post[np.clip(ni, 0, T - 1).astype(int)], np.nan)
    span = np.maximum(pi * 0 + (prev_idx + next_idx), 1)
    w = np.clip(prev_idx / span, 0, 1)
    both = np.isfinite(p_v) & np.isfinite(n_v)
    return np.where(both, p_v + (n_v - p_v) * w, np.where(np.isfinite(p_v), p_v, n_v))


def _wmean(vals, ok, w):
    num = np.where(ok, vals * w, 0.0).sum(1)
    den = (ok * w).sum(1)
    return np.divide(num, den, out=np.full(len(num), np.nan), where=den > 0)


def build_features(a, ctx: Ctx, nt):
    """a: matriz (días, estaciones) con huecos.  Devuelve dict nombre -> matriz (días, estaciones)."""
    T, S = a.shape
    f = {k: np.full((T, S), np.nan) for k in FEATURES}
    f["long_hueco"] = gap_lengths(np.isnan(a)).astype(float)
    temp = ctx.var in TEMPS
    for j in range(S):
        f["sin_doy"][:, j], f["cos_doy"][:, j] = ctx.sin, ctx.cos
        f["altitud"][:, j], f["latitud"][:, j], f["longitud"][:, j] = ctx.alt[j], ctx.lat[j], ctx.lon[j]
        f["clima_propio"][:, j] = ctx.clim[:, j]
        (f["prev_val"][:, j], f["next_val"][:, j], f["dias_prev"][:, j],
         f["dias_next"][:, j], f["lineal"][:, j]) = _own(a[:, j])
        ids = nt.ids[j]
        f["n_vecinos"][:, j] = 0
        f["suma_corr"][:, j] = 0.0
        if len(ids) == 0:
            continue
        vals = a[:, ids]
        ok = np.isfinite(vals)
        n = ok.sum(1)
        f["n_vecinos"][:, j] = n
        d, c = nt.dist[j, ids], nt.corr[j, ids]
        dalt = np.abs(ctx.alt[j] - ctx.alt[ids])
        w_idw = 1 / np.maximum(d, 1) ** 2
        w_cor = np.maximum(c, 0.01) / (1 + d / 100 + dalt / 300)
        f["idw"][:, j] = _wmean(vals, ok, w_idw)
        f["pond_corr"][:, j] = _wmean(vals, ok, w_cor)
        if temp:   # transferencia de anomalía: normal propia + anomalía ponderada de las vecinas
            f["est_vecinos"][:, j] = ctx.clim[:, j] + _wmean(vals - ctx.clim[:, ids], ok, w_cor)
            nbm = _wmean(vals, ok, w_cor)
            f["off_local"][:, j] = _local_offset(a[:, j], nbm, f["dias_prev"][:, j], f["dias_next"][:, j])
            f["est_local"][:, j] = nbm + f["off_local"][:, j]
        else:      # razón normal: valor de la vecina escalado por la relación de normales
            ratio = (ctx.clim[:, [j]] + 0.1) / (ctx.clim[:, ids] + 0.1)
            f["est_vecinos"][:, j] = _wmean(vals * ratio, ok, w_cor)
            f["frac_lluvia"][:, j] = np.divide(((vals >= RAIN_MM) & ok).sum(1), n, out=np.full(T, np.nan), where=n > 0)
        for name, arr in (("dist_media", d), ("dalt_media", dalt), ("corr_media", c)):
            f[name][:, j] = np.divide((ok * arr).sum(1), n, out=np.full(T, np.nan), where=n > 0)
        f["suma_corr"][:, j] = (ok * c).sum(1)
        dmin = np.where(ok, d, np.inf).min(1)
        f["dist_min"][:, j] = np.where(np.isfinite(dmin), dmin, np.nan)
        bs = ctx.basin
        same = np.ones(len(ids)) if bs is None or bs[j] < 0 else np.where(bs[ids] < 0, 1.0, (bs[ids] == bs[j]).astype(float))
        f["frac_misma_cuenca"][:, j] = np.divide((ok * same).sum(1), n, out=np.full(T, np.nan), where=n > 0)
        mx = np.where(ok, vals, -np.inf).max(1)
        f["max_vecinos"][:, j] = np.where(np.isfinite(mx), mx, np.nan)
    return f


class FeatureSet:
    """Filas (estación, día) con sus features.  fs['idw'] da la columna; fs.X la matriz para los modelos."""

    def __init__(self, cols, t, j, months):
        self.cols, self.t, self.j, self.months = cols, t, j, months
        self.gap_type = gap_type(cols["long_hueco"])
        self.season = np.where(np.isin(months, RAIN_MONTHS), "lluvias", "estiaje")

    @classmethod
    def from_mask(cls, feat, mask, ctx):
        t, j = np.nonzero(mask)
        return cls({k: v[t, j] for k, v in feat.items()}, t, j, ctx.months[t])

    @property
    def X(self):
        return np.column_stack([self.cols[k] for k in FEATURES])

    def __getitem__(self, k):
        return self.cols[k]

    def __len__(self):
        return len(self.t)

    @classmethod
    def concat(cls, parts):
        cols = {k: np.concatenate([p.cols[k] for p in parts]) for k in FEATURES}
        return cls(cols, np.concatenate([p.t for p in parts]), np.concatenate([p.j for p in parts]),
                   np.concatenate([p.months for p in parts]))

    def estimable(self, cfg):
        """Regla de 'cuándo rellenar': hueco no excesivo Y (vecinas suficientes, o hueco corto con serie propia a ambos lados)."""
        short_ok = (self["long_hueco"] <= 3) & np.isfinite(self["lineal"])
        enough = self["n_vecinos"] >= cfg.min_neighbors_estimate
        return (self["long_hueco"] <= cfg.max_fill_days) & (enough | short_ok)
