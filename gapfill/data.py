"""Carga de datos, control de calidad (QC) y reconstrucción del calendario.

Convención: cada variable es una matriz (días, estaciones).  NaN = no hay dato válido.

QC en dos niveles:
  * INVÁLIDO (se descarta antes de entrenar y de usar como vecina): código de faltante, valor fuera de rango físico,
    lluvia negativa o > rain_max, Tmax < Tmin.
  * ATÍPICO SEVERO (también se descarta, porque contamina vecinas y entrenamiento): sensor pegado, racha larga de
    ceros en temporada de lluvias, saltos extremos contra la mediana mensual o móvil, rango diurno > dtr_max.
    Todo se cuenta en qc_log y queda marcado en 'invalido' / 'sospechoso' de la salida; el valor original se
    conserva siempre en 'valor_original'.
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import RAIN_MONTHS, TEMPS, VARS, Config


@dataclass
class Dataset:
    stations: pd.DataFrame
    dates: pd.DatetimeIndex
    values: dict        # variable -> matriz limpia (NaN donde falta o es inválido; objetivos públicos ocultos)
    raw: dict           # variable -> matriz tal como venía (para conservar el dato original)
    invalid: dict       # variable -> bool: valor descartado por QC (se trata como faltante)
    suspicious: dict    # variable -> bool: atípico (la parte severa también está en 'invalid')
    active: np.ndarray  # bool: la estación estaba en operación ese día
    public: pd.DataFrame
    qc_log: dict
    diag_values: dict = None   # variable -> matriz post-QC ANTES de ocultar los objetivos públicos (para el diagnóstico)
    raw_missing: dict = None   # variable -> bool: faltante en el archivo original (sin fila, vacío o código)
    basin: np.ndarray = None   # id de cuenca por estación (covariables.gpkg), -1 si no hay


def _runs(flag):
    return np.flatnonzero(np.diff(np.r_[False, flag, False].astype(np.int8))).reshape(-1, 2)


def _atypical(var, x, dates, cfg):
    """Devuelve (sospechoso, severo): severo ⊂ sospechoso y es lo que se descarta."""
    sus, sev = np.zeros(x.shape, bool), np.zeros(x.shape, bool)
    month = dates.month.to_numpy()
    for j in range(x.shape[1]):
        col = pd.Series(x[:, j], index=dates)
        v = x[:, j]
        if var == "precipitacion":
            # Ojo: una racha larga de ceros NO es en sí sospechosa (el Bajío tiene sequías de medio verano y lluvias tardías).
            # Se evalúa contra las vecinas en _ceros_contra_vecinas(), porque solo es dudosa si ellas sí registraron lluvia.
            # sensor pegado con lluvia: el mismo valor positivo N días seguidos es casi imposible
            same = np.r_[False, (np.diff(v) == 0) & (v[1:] > 0)]
            for s, e in _runs(same):
                if e - s + 1 >= cfg.flatline_days:
                    sus[max(s - 1, 0):e, j] = True
                    sev[max(s - 1, 0):e, j] = True
        else:
            same = np.r_[False, np.diff(v) == 0]
            for s, e in _runs(same):
                if e - s + 1 >= cfg.flatline_days:
                    sus[max(s - 1, 0):e, j] = True
                    sev[max(s - 1, 0):e, j] = True
            med = col.rolling(31, center=True, min_periods=10).median()
            spike = ((col - med).abs() > cfg.spike_c).to_numpy()
            sus[:, j] |= spike
            sev[:, j] |= spike
            # z-score robusto por mes calendario de la propia estación (mediana y MAD)
            g = col.groupby(month)
            mm = g.transform("median")
            mad = (col - mm).abs().groupby(month).transform("median") * 1.4826
            z = ((col - mm).abs() / mad.replace(0, np.nan)).to_numpy()
            out = (z > cfg.robust_z) & ((col - mm).abs() > cfg.robust_abs).to_numpy()
            sus[:, j] |= out
            sev[:, j] |= out
    return sus, sev


def _ceros_contra_vecinas(x, dates, cfg):
    """Ceros sospechosos: racha de >= zero_run_days ceros en temporada de lluvias mientras las demás estaciones SÍ registran
    lluvia (>= zero_wet_frac de los días de la racha con lluvia, en promedio). Con una sola estación no hay evidencia: no se marca.
    Devuelve una máscara (días, estaciones)."""
    out = np.zeros(x.shape, bool)
    S = x.shape[1]
    if S < 2:
        return out
    month = dates.month.to_numpy()
    for j in range(S):
        others = np.delete(x, j, axis=1)
        for s, e in _runs(x[:, j] == 0):
            if e - s < cfg.zero_run_days:
                continue
            inr = np.isin(month[s:e], RAIN_MONTHS)
            if not inr.any():
                continue
            seg = others[s:e]
            n_ok = np.isfinite(seg).sum(axis=1)
            ok = n_ok > 0
            wet = np.where(ok, (np.nan_to_num(seg, nan=0.0) >= 0.1).sum(axis=1) / np.maximum(n_ok, 1), 0.0)  # fracción de vecinas con lluvia
            if ok.sum() >= cfg.zero_run_days // 2 and wet[ok].mean() >= cfg.zero_wet_frac:
                out[s:e, j] |= inr
    return out


def _read_table(path):
    path = Path(path)
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype={"clave_estacion": str})


def _find(d, stem):
    for ext in (".csv", ".parquet", ".geojson", ".json"):
        if (d / f"{stem}{ext}").exists():
            return d / f"{stem}{ext}"
    return None


def _read_stations(d):
    p = _find(d, "estaciones")
    if p is None:
        raise FileNotFoundError("Falta estaciones.csv / estaciones.geojson en el directorio de datos")
    if p.suffix in (".geojson", ".json"):
        import geopandas as gpd   # opcional: solo para GeoJSON
        g = gpd.read_file(p)
        g["longitud"], g["latitud"] = g.geometry.x, g.geometry.y
        st = pd.DataFrame(g.drop(columns="geometry"))
        st["clave_estacion"] = st["clave_estacion"].astype(str)
    else:
        st = _read_table(p)
    return st.sort_values("clave_estacion").reset_index(drop=True)


def _basins(st, d):
    """Cuenca de cada estación (covariables.gpkg, capa con polígonos). Opcional: si falta algo, -1 para todas."""
    gp = d / "covariables.gpkg"
    out = np.full(len(st), -1)
    if not gp.exists():
        return out
    try:
        import geopandas as gpd
        from shapely.geometry import Point
        import pyogrio
        layers = [l[0] for l in pyogrio.list_layers(gp)]
        for l in layers:
            g = gpd.read_file(gp, layer=l)
            if g.geom_type.astype(str).str.contains("Polygon").all() and len(g):
                pts = [Point(x, y) for x, y in zip(st.longitud, st.latitud)]
                for i, p in enumerate(pts):
                    hit = np.flatnonzero(g.contains(p).to_numpy())
                    if len(hit):
                        out[i] = int(hit[0])
                break
    except Exception:
        return np.full(len(st), -1)
    return out


def _read_public(d, dates, keys, raw_wide=None):
    """Objetivos a estimar. Formato largo (clave_estacion, fecha, variable) o ancho (clave_estacion, fecha[, columnas
    de variables: las celdas vacías son los objetivos]; sin columnas de variables = las tres)."""
    p = _find(d, "test_publico")
    if p is None:
        return pd.DataFrame(columns=["clave_estacion", "fecha", "variable"])
    pub = _read_table(p)
    pub["clave_estacion"] = pub["clave_estacion"].astype(str).str.strip()
    pub["fecha"] = pd.to_datetime(pub["fecha"]).dt.normalize()
    if "variable" in pub.columns:
        return pub[["clave_estacion", "fecha", "variable"]]
    cols = [v for v in VARS if v in pub.columns]
    if not cols:
        return pd.concat([pub[["clave_estacion", "fecha"]].assign(variable=v) for v in VARS], ignore_index=True)
    return pd.concat([pub[pub[v].isna() | (pub[v] == -9999)][["clave_estacion", "fecha"]].assign(variable=v) for v in cols],
                     ignore_index=True)


def _canonical(d):
    """Paquete del reto (estaciones + series_diarias) si está; si no, CLICOM en CSV/TXT (ver ingesta.py)."""
    if _find(d, "series_diarias") is not None and _find(d, "estaciones") is not None:
        return _read_stations(d), _read_table(_find(d, "series_diarias")), None
    from .ingesta import cargar_clicom
    got = cargar_clicom(d)
    if got is None:
        raise FileNotFoundError(f"No encontré datos en {d}: espero estaciones.csv + series_diarias.csv, o archivos CLICOM (.csv/.txt)")
    return got


def load_dataset(data_dir, cfg: Config) -> Dataset:
    d = Path(data_dir)
    st, raw, ingesta = _canonical(d)
    raw = raw[["clave_estacion", "fecha", *[v for v in VARS if v in raw.columns]]].copy()
    raw["clave_estacion"] = raw["clave_estacion"].astype(str)
    raw["fecha"] = pd.to_datetime(raw["fecha"]).dt.normalize()
    log = {"filas_crudas": int(len(raw))}
    if ingesta:
        log["ingesta"] = ingesta

    exact = raw.duplicated(keep="first")
    log["duplicados_exactos_eliminados"] = int(exact.sum())
    raw = raw[~exact]
    conflict = raw.duplicated(["fecha", "clave_estacion"], keep=False)
    log["duplicados_conflictivos_descartados"] = int(conflict.sum())
    raw = raw[~conflict]

    st["clave_estacion"] = st["clave_estacion"].astype(str)
    keys = st.clave_estacion.tolist()
    public = _read_public(d, None, keys)
    lo_d, hi_d = raw.fecha.min(), raw.fecha.max()
    if len(public):   # si el comité pide fechas fuera del rango de la serie, el calendario se extiende (esas fechas son huecos reales)
        lo_d, hi_d = min(lo_d, public.fecha.min()), max(hi_d, public.fecha.max())
    dates = pd.date_range(lo_d, hi_d)
    lo, hi = cfg.temp_range
    values, rawv, invalid, rawmiss = {}, {}, {}, {}
    for v in VARS:
        x = (raw.pivot(index="fecha", columns="clave_estacion", values=v)
             .reindex(index=dates, columns=keys).apply(pd.to_numeric, errors="coerce").to_numpy(float, copy=True))
        rawv[v] = x.copy()
        with np.errstate(invalid="ignore"):
            code = np.isin(x, cfg.missing_codes)
            log[f"{v}_codigos_de_faltante"] = int(code.sum())
            x[code] = np.nan
            bad = (x < 0) | (x > cfg.rain_max) if v == "precipitacion" else ((x < lo) | (x > hi))
        rawmiss[v] = np.isnan(rawv[v]) | code
        invalid[v] = bad
        log[f"{v}_fuera_de_rango"] = int(bad.sum())
        x[bad] = np.nan
        values[v] = x

    # periodo de operación: columnas explícitas si vienen; si no, se infiere de la primera/última observación válida
    active = np.ones((len(dates), len(st)), bool)
    has_cols = "inicio_operacion" in st or "fin_operacion" in st
    log["operacion_inferida_de_datos"] = not has_cols
    anyobs = np.isfinite(np.stack([values[v] for v in VARS])).any(0)
    for j, r in st.iterrows():
        s0 = pd.Timestamp(r["inicio_operacion"]) if has_cols and pd.notna(r.get("inicio_operacion")) else None
        e0 = pd.Timestamp(r["fin_operacion"]) if has_cols and pd.notna(r.get("fin_operacion")) else None
        if s0 is None or e0 is None:
            idx = np.flatnonzero(anyobs[:, j])
            if len(idx):
                s0 = s0 or dates[idx[0]]
                e0 = e0 or dates[idx[-1]]
        if s0 is not None:
            active[:, j] &= dates >= s0
        if e0 is not None:
            active[:, j] &= dates <= e0
    for v in VARS:
        values[v][~active] = np.nan

    with np.errstate(invalid="ignore"):
        swap = values["tmax"] < values["tmin"]
        wide = (values["tmax"] - values["tmin"]) > cfg.dtr_max
    log["tmax_menor_que_tmin"] = int(swap.sum())
    log["rango_diurno_excesivo"] = int(wide.sum())
    for v in TEMPS:
        invalid[v] = invalid[v] | swap | wide
        values[v][swap | wide] = np.nan

    suspicious = {}
    for v in VARS:
        sus, sev = _atypical(v, values[v], dates, cfg)
        suspicious[v] = sus
        log[f"{v}_atipicos_severos_descartados"] = int((sev & np.isfinite(values[v])).sum())
        invalid[v] = invalid[v] | sev
        values[v][sev] = np.nan
        log[f"{v}_sospechosos"] = int(sus.sum())

    zr = _ceros_contra_vecinas(values["precipitacion"], dates, cfg)
    suspicious["precipitacion"] |= zr
    invalid["precipitacion"] = invalid["precipitacion"] | zr
    values["precipitacion"][zr] = np.nan
    log["precipitacion_ceros_incompatibles_con_vecinas"] = int(zr.sum())
    log["precipitacion_atipicos_severos_descartados"] += int(zr.sum())
    log["precipitacion_sospechosos"] = int(suspicious["precipitacion"].sum())

    diag_values = {v: values[v].copy() for v in VARS}      # el diagnóstico ve los datos ANTES de esconder los objetivos
    if len(public):
        ti = dates.get_indexer(public.fecha)
        kj = pd.Index(keys).get_indexer(public.clave_estacion)
        if (ti < 0).any() or (kj < 0).any():
            raise ValueError("test_publico tiene fechas o estaciones fuera de los datos")
        for v, g in public.assign(ti=ti, kj=kj).groupby("variable"):
            values[v][g.ti.to_numpy(), g.kj.to_numpy()] = np.nan   # los objetivos nunca se ven
    log["objetivos_a_estimar"] = int(len(public))
    return Dataset(st, dates, values, rawv, invalid, suspicious, active, public, log, diag_values, rawmiss, _basins(st, d))
