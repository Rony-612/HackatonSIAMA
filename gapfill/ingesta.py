"""Ingesta tolerante: del formato que llegue al contrato canónico (estaciones + series diarias).

Formatos soportados (se detectan solos, en este orden):
  1. Paquete del reto:  estaciones.csv|geojson + series_diarias.csv|parquet           (lo maneja data.py)
  2. CLICOM en CSV:     *.csv con columnas fecha, clave_estacion, [nombre, latitud, longitud, altitud], precip_mm, tmax_c, tmin_c
  3. CLICOM/CICLOM TXT: dia<clave>.txt de la Base de Datos Climatológica Nacional (CONAGUA/SMN): encabezado con metadatos
                        (ESTACIÓN, NOMBRE, MUNICIPIO, SITUACIÓN, LATITUD, LONGITUD, ALTITUD) y tabla FECHA PRECIP EVAP TMAX TMIN,
                        donde 'NULO' es dato faltante.
Cuando hay varias fuentes de la misma estación (p. ej. CSV 1981-2025 y TXT 1941-2025) se UNEN: el día que una fuente
no trae se completa con la otra, y si dos fuentes traen valores distintos para el mismo día gana la primera de la
lista de prioridad (TXT original > CSV) y el conflicto se cuenta en el registro de ingesta.
Nunca se inventa nada: solo se leen, se normalizan nombres/unidades y se reconstruye el calendario diario continuo.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

VARS_CAN = ("precipitacion", "tmax", "tmin")
_SIN = {
    "fecha": ["fecha", "date", "dia", "time", "timestamp"],
    "clave_estacion": ["clave_estacion", "clave", "estacion", "id_estacion", "station", "station_id", "cve"],
    "nombre": ["nombre", "name", "estacion_nombre"],
    "latitud": ["latitud", "latitude", "lat", "y"],
    "longitud": ["longitud", "longitude", "lon", "long", "x"],
    "altitud": ["altitud", "elevation", "elev", "alt", "msnm", "z"],
    "municipio": ["municipio"],
    "precipitacion": ["precipitacion", "precip_mm", "precip", "pcp", "prcp", "lluvia", "rain", "precipitation_sum"],
    "tmax": ["tmax", "tmax_c", "temp_max", "t_max", "tmaxima", "temperatura_maxima"],
    "tmin": ["tmin", "tmin_c", "temp_min", "t_min", "tminima", "temperatura_minima"],
}
_NULOS = {"NULO", "NULL", "NAN", "NA", "N/A", "S/D", "SD", "", "-", "--"}


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def _leer_texto(path):
    """UTF-8 con respaldo a Latin-1/CP1252 (los archivos oficiales a veces traen acentos mal codificados)."""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _mapear_columnas(cols):
    """Nombre canónico de cada columna reconocida (insensible a mayúsculas, acentos y símbolos)."""
    out, usadas = {}, set()
    for c in cols:
        n = _norm(c)
        for canon, alias in _SIN.items():
            if canon not in usadas and n in alias:
                out[c], _ = canon, usadas.add(canon)
                break
    return out


# ───────────────────────── CSV tipo CLICOM ─────────────────────────
def es_clicom_csv(path) -> bool:
    try:
        cab = pd.read_csv(path, nrows=0, sep=None, engine="python", encoding="utf-8-sig").columns
    except Exception:
        return False
    m = set(_mapear_columnas(cab).values())
    return {"fecha", "clave_estacion"} <= m and len(m & set(VARS_CAN)) >= 1


def leer_clicom_csv(path):
    import io
    df = pd.read_csv(io.StringIO(_leer_texto(path)), sep=None, engine="python", dtype=str)
    df = df.rename(columns=_mapear_columnas(df.columns))
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce").dt.normalize()
    df = df[df.fecha.notna()].copy()
    df["clave_estacion"] = df["clave_estacion"].astype(str).str.strip().str.lstrip("0").replace("", "0")
    for v in VARS_CAN:
        df[v] = pd.to_numeric(df[v].replace(list(_NULOS), np.nan), errors="coerce") if v in df else np.nan
    est = {}
    for k, g in df.groupby("clave_estacion"):
        r = g.iloc[0]
        est[k] = {"clave_estacion": k, "nombre": r.get("nombre", k), "municipio": r.get("municipio", "Desconocido"),
                  "latitud": pd.to_numeric(r.get("latitud"), errors="coerce"), "longitud": pd.to_numeric(r.get("longitud"), errors="coerce"),
                  "altitud": pd.to_numeric(r.get("altitud"), errors="coerce")}
    return pd.DataFrame(est.values()), df[["clave_estacion", "fecha", *VARS_CAN]], Path(path).name


# ───────────────────────── TXT de la Base de Datos Climatológica Nacional ─────────────────────────
_FILA = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")
_META = re.compile(r"^\s*([A-ZÁÉÍÓÚÑ\.\- ]+?)\s*:\s*(.*?)\s*$")


def es_clicom_txt(path) -> bool:
    try:
        cab = _leer_texto(path)[:3000].upper()
    except Exception:
        return False
    return "FECHA" in cab and "PRECIP" in cab and ("ESTACI" in cab)


def _num(x, sufijo=""):
    m = re.search(r"-?\d+(?:\.\d+)?", x or "")
    return float(m.group()) if m else np.nan


def leer_clicom_txt(path):
    meta, filas = {}, []
    for ln in _leer_texto(path).splitlines():
        m = _FILA.match(ln)
        if m:
            filas.append(m.groups())
            continue
        mm = _META.match(ln)
        if mm and not filas:
            meta[_norm(mm.group(1))] = mm.group(2)
    if not filas:
        raise ValueError(f"{Path(path).name}: no encontré filas de datos (FECHA PRECIP EVAP TMAX TMIN)")
    d = pd.DataFrame(filas, columns=["fecha", "precipitacion", "evap", "tmax", "tmin"])
    d["fecha"] = pd.to_datetime(d["fecha"], errors="coerce").dt.normalize()
    for c in ("precipitacion", "evap", "tmax", "tmin"):
        d[c] = pd.to_numeric(d[c].where(~d[c].str.upper().isin(_NULOS)), errors="coerce")
    clave = str(meta.get("estacion", "")).strip().lstrip("0") or Path(path).stem
    d["clave_estacion"] = clave
    dep = meta.get("u_admnva", "")
    est = {"clave_estacion": clave, "nombre": meta.get("nombre", clave).title(), "municipio": meta.get("municipio", "Desconocido").title(),
           "latitud": _num(meta.get("latitud")), "longitud": _num(meta.get("longitud")), "altitud": _num(meta.get("altitud")),
           "estado_operativo": meta.get("situacion", "").lower() or "desconocido",
           "dependencia": ("Comisión Nacional del Agua" + (f" · {dep.title()}" if dep else "")) if "COMISI" in _leer_texto(path)[:400].upper() else (dep.title() or "No especificada")}
    return pd.DataFrame([est]), d[["clave_estacion", "fecha", "precipitacion", "tmax", "tmin"]], Path(path).name


# ───────────────────────── orquestación ─────────────────────────
def descubrir(d):
    d = Path(d)
    txt = sorted(p for p in d.rglob("*.txt") if es_clicom_txt(p))
    csv = sorted(p for p in d.rglob("*.csv") if p.name not in ("estaciones.csv", "series_diarias.csv", "test_publico.csv") and es_clicom_csv(p))
    return txt, csv


def cargar_clicom(d):
    """Devuelve (estaciones, series, registro). Prioridad: TXT original > CSV; un día faltante en una fuente se completa con otra."""
    txts, csvs = descubrir(d)
    if not txts and not csvs:
        return None
    est_parts, ser_parts, fuentes = [], [], []
    for prio, (fn, paths) in enumerate(((leer_clicom_txt, txts), (leer_clicom_csv, csvs))):
        for p in paths:
            e, s, nom = fn(p)
            e["_prio"] = prio
            s = s.assign(_prio=prio)
            est_parts.append(e)
            ser_parts.append(s)
            fuentes.append({"archivo": nom, "tipo": "txt" if prio == 0 else "csv", "filas": int(len(s)), "estaciones": e.clave_estacion.tolist(),
                            "desde": s.fecha.min().strftime("%Y-%m-%d"), "hasta": s.fecha.max().strftime("%Y-%m-%d")})
    est = pd.concat(est_parts, ignore_index=True).sort_values("_prio")
    # metadatos: el primer valor no nulo por columna (TXT primero: trae municipio, situación, dependencia)
    est = est.groupby("clave_estacion", sort=True).agg(lambda s: s.dropna().iloc[0] if s.notna().any() else np.nan).drop(columns="_prio").reset_index()

    ser = pd.concat(ser_parts, ignore_index=True).sort_values(["clave_estacion", "fecha", "_prio"])
    reg = {"fuentes": fuentes, "conflictos_entre_fuentes": 0, "dias_completados_con_otra_fuente": 0, "fechas_duplicadas_en_una_fuente": 0}
    out = []
    for k, g in ser.groupby("clave_estacion"):
        dup = g.duplicated(["fecha", "_prio"], keep=False)
        reg["fechas_duplicadas_en_una_fuente"] += int(dup.sum())
        # una fuente con la misma fecha repetida: promedio de los no nulos (nunca se descarta un registro)
        g = g.groupby(["fecha", "_prio"], as_index=False)[list(VARS_CAN)].mean()
        base = g[g._prio == g._prio.min()].set_index("fecha")[list(VARS_CAN)]
        res = base.copy()
        for prio in sorted(g._prio.unique())[1:]:
            o = g[g._prio == prio].set_index("fecha")[list(VARS_CAN)]
            res = res.reindex(res.index.union(o.index))
            for v in VARS_CAN:
                ov = o[v].reindex(res.index)
                both = res[v].notna() & ov.notna()
                reg["conflictos_entre_fuentes"] += int((both & ((res[v] - ov).abs() > 1e-6)).sum())
                fill = res[v].isna() & ov.notna()
                reg["dias_completados_con_otra_fuente"] += int(fill.sum())
                res.loc[fill, v] = ov[fill]
        res = res.sort_index()
        res.index.name = "fecha"
        out.append(res.reset_index().assign(clave_estacion=k))
    series = pd.concat(out, ignore_index=True)[["clave_estacion", "fecha", *VARS_CAN]]

    # periodo de operación: desde la primera fila de la fuente hasta el final de la base si la estación figura como OPERANDO
    fin_base = series.fecha.max()
    est["inicio_operacion"] = est.clave_estacion.map(series.groupby("clave_estacion").fecha.min())
    est["fin_operacion"] = [fin_base if str(s).lower().startswith("operando") else series[series.clave_estacion == k].fecha.max()
                            for k, s in zip(est.clave_estacion, est.get("estado_operativo", pd.Series([""] * len(est))))]
    for c, v in (("municipio", "Desconocido"), ("dependencia", "No especificada"), ("estado_operativo", "desconocido")):
        est[c] = est[c].fillna(v) if c in est else v
    # saneamiento geodésico: Guanajuato está en longitud negativa; si llega con signo positivo se corrige y se anota
    pos = est.longitud > 0
    reg["longitudes_con_signo_corregido"] = int(pos.sum())
    est.loc[pos, "longitud"] = -est.loc[pos, "longitud"]
    for c in ("latitud", "longitud"):
        if est[c].isna().any():
            raise ValueError(f"Faltan coordenadas ({c}) para: {est.loc[est[c].isna(), 'clave_estacion'].tolist()}")
    est["altitud"] = est["altitud"].fillna(est["altitud"].median())
    reg["estaciones"] = est.clave_estacion.tolist()
    return est.sort_values("clave_estacion").reset_index(drop=True), series, reg
