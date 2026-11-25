"""API REST de solo lectura de GeoTrust SIAMA: sirve lo que dejó el pipeline en outputs/.  NO predice en vivo.

Todas las respuestas tienen esquema Pydantic (visible en /docs y exportable con scripts/export_openapi.py).
Un dato estimado NUNCA se presenta como observado: cada punto lleva 'origen' y 'calidad'.
"""
import json
import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from . import llm
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.getenv("SIAMA_OUTPUT_DIR", ROOT / "outputs"))
Variable = Literal["precipitacion", "tmax", "tmin"]

BANDERAS = {
    "observado": "Dato medido por la estación que pasó el control de calidad.",
    "estimado_alta": "Estimado con respaldo de vecinas fuerte (≥3 observadas, buena correlación, hueco ≤30 d), intervalo estrecho y sin cambio abrupto en la estación.",
    "estimado_baja": "Estimado con poco respaldo, hueco largo, intervalo ancho o estación con cambio abrupto: úselo con cautela.",
    "no_estimable": "No se rellenó: no hay información suficiente para estimarlo con honestidad.",
}
UNIDADES = {"precipitacion": "mm", "tmax": "°C", "tmin": "°C"}
NOMBRES = {"precipitacion": "Precipitación diaria", "tmax": "Temperatura máxima", "tmin": "Temperatura mínima"}

app = FastAPI(
    title="GeoTrust SIAMA · Datos climatológicos sin huecos",
    version="2.0.0",
    description="Series diarias completadas con ML. Cada dato trae su origen (observado/estimado/no_estimable), su bandera "
                "de calidad y, si es estimado, su intervalo de incertidumbre (nominal 90 %) y su trazabilidad.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"])
V1 = "/api/v1"


# ───────────────────────── esquemas ─────────────────────────
class Punto(BaseModel):
    fecha: str
    valor: Optional[float] = None
    limite_inferior: Optional[float] = None
    limite_superior: Optional[float] = None
    origen: Literal["observado", "estimado", "no_estimable"]
    calidad: str
    metodo: str
    motivo_no_estimable: str
    longitud_hueco: int
    vecinos_disponibles: int
    sospechoso: bool


class SerieOut(BaseModel):
    clave_estacion: str
    municipio: str
    variable: str
    etiqueta: str
    unidad: str
    nivel_intervalo: Optional[float]
    conteos: dict
    total: int
    offset: int
    devueltos: int
    siguiente_offset: Optional[int]
    puntos: list[Punto]


class EstacionMapa(BaseModel):
    clave_estacion: str
    nombre: str
    municipio: str
    latitud: float
    longitud: float
    altitud: float
    porcentaje_vacios: Optional[float]
    hueco_mas_largo: Optional[int]
    cambio_abrupto: bool


class EstacionesOut(BaseModel):
    variable: str
    etiqueta: str
    unidad: str
    estaciones: list[EstacionMapa]


class Hueco(BaseModel):
    inicio: str
    fin: str
    longitud: int
    tipo: str


class DiagnosticoOut(BaseModel):
    clave_estacion: str
    variable: str
    esperados: int
    faltantes: int
    porcentaje_vacios: float
    porcentaje_vacios_crudo: float
    cortos: int
    medios: int
    largos: int
    hueco_mas_largo: int
    invalidos_o_descartados_qc: int
    sospechosos: int
    faltantes_por_anio: dict
    faltantes_por_mes: dict
    huecos: Optional[list[Hueco]] = None


class VecinaDia(BaseModel):
    clave_estacion: str
    distancia_km: float
    correlacion: float
    diferencia_altitud_m: float
    misma_cuenca: Optional[bool] = None
    valor_observado: Optional[float] = None


class TrazabilidadOut(BaseModel):
    fecha: str
    origen: str
    calidad: str
    metodo: str
    motivo_no_estimable: str
    longitud_hueco: int
    vecinas_con_dato_ese_dia: list[VecinaDia]
    nota: str


class RecomendacionOut(BaseModel):
    variable: str
    longitud_hueco: int
    rango: str
    mejor: str
    mae: dict
    n: int
    metodo_activo_en_produccion: str
    nota: str


class Homogeneidad(BaseModel):
    clave_estacion: str
    variable: str
    estadistico_T: float
    critico_aprox: float
    posible_cambio: bool
    fecha_probable: Optional[str] = None
    salto_medio: float
    unidad_salto: str
    meses_usados: int


class PreguntaIn(BaseModel):
    pregunta: str = Field(..., min_length=3, max_length=400, examples=["¿Qué tan completa está la estación SIM001?"])
    clave_estacion: Optional[str] = None
    variable: Optional[Variable] = None
    fecha: Optional[str] = Field(None, description="AAAA-MM-DD")


class RespuestaAsistente(BaseModel):
    intencion: str
    respuesta: str
    fuentes: list[str]
    datos: dict
    aviso: str = "Respuesta construida solo con los archivos de resultados del pipeline; no se inventa ningún dato."
    generado_por: str = "reglas deterministas"
    verificacion: str = "no aplica"
    respuesta_base: Optional[str] = Field(None, description="Texto determinista por reglas (referencia cuando redacta Granite)")


# ───────────────────────── almacén ─────────────────────────
def _nn(df):
    return json.loads(df.to_json(orient="records"))   # NaN -> null


@lru_cache(maxsize=1)
def _store(stamp):
    if not (OUT / "metricas.json").exists():
        raise HTTPException(503, "Aún no hay resultados: ejecuta  python -m gapfill.cli")
    s = pd.read_csv(OUT / "series_completadas.csv", dtype={"clave_estacion": str}, parse_dates=["fecha"])
    s["motivo_no_estimable"] = s["motivo_no_estimable"].fillna("")
    s["metodo"] = s["metodo"].fillna("")
    hp = OUT / "homogeneidad.csv"
    hom = pd.read_csv(hp, dtype={"clave_estacion": str}).fillna({"fecha_probable": ""}) if hp.exists() else pd.DataFrame()
    return {
        "est": pd.read_csv(OUT / "estaciones.csv", dtype={"clave_estacion": str}),
        "diag": pd.read_csv(OUT / "diagnostico.csv", dtype={"clave_estacion": str}),
        "huecos": pd.read_csv(OUT / "huecos.csv", dtype={"clave_estacion": str}),
        "groups": {k: g.reset_index(drop=True) for k, g in s.groupby(["clave_estacion", "variable"])},
        "metricas": json.loads((OUT / "metricas.json").read_text()),
        "vecinos": json.loads((OUT / "vecinos.json").read_text()),
        "proyecto": json.loads((OUT / "proyecto.json").read_text()),
        "hom": hom,
    }


def S():
    return _store((OUT / "metricas.json").stat().st_mtime_ns if (OUT / "metricas.json").exists() else 0)


def _estacion(eid):
    e = S()["est"]
    r = e[e.clave_estacion == eid]
    if r.empty:
        raise HTTPException(404, f"La estación '{eid}' no existe")
    return r.iloc[0]


def _rango(n):
    return "1-3" if n <= 3 else "4-30" if n <= 30 else "31-60" if n <= 60 else "61+"


def _cambios():
    h = S()["hom"]
    return set() if h.empty else {(r.clave_estacion, r.variable) for r in h.itertuples() if r.posible_cambio}


# ───────────────────────── meta ─────────────────────────
@app.get("/health", tags=["meta"])
def health():
    return {"estado": "ok", "proyecto": S()["proyecto"]["nombre"], "estaciones": len(S()["est"])}


@app.get(f"{V1}/proyecto", tags=["meta"], summary="Identidad y método del proyecto")
def proyecto():
    return S()["proyecto"]


@app.get(f"{V1}/banderas", tags=["meta"], summary="Significado de cada bandera de calidad", response_model=dict[str, str])
def banderas():
    return BANDERAS


# ───────────────────────── diagnóstico ─────────────────────────
@app.get(f"{V1}/estaciones", tags=["diagnóstico"], response_model=EstacionesOut,
         summary="Estaciones con su % de vacíos (para el mapa)")
def estaciones(variable: Variable = "precipitacion"):
    d = S()["diag"]
    d = d[d.variable == variable].set_index("clave_estacion")
    cam = _cambios()
    out = []
    for r in S()["est"].itertuples():
        x = d.loc[r.clave_estacion] if r.clave_estacion in d.index else None
        out.append({"clave_estacion": r.clave_estacion, "nombre": r.nombre, "municipio": r.municipio,
                    "latitud": r.latitud, "longitud": r.longitud, "altitud": r.altitud,
                    "porcentaje_vacios": None if x is None else float(x.porcentaje_vacios),
                    "hueco_mas_largo": None if x is None else int(x.hueco_mas_largo),
                    "cambio_abrupto": (r.clave_estacion, variable) in cam})
    return {"variable": variable, "etiqueta": NOMBRES[variable], "unidad": UNIDADES[variable], "estaciones": out}


@app.get(f"{V1}/estaciones/{{estacion}}/diagnostico", tags=["diagnóstico"], response_model=DiagnosticoOut,
         summary="Diagnóstico de vacíos de una estación (endpoint 1)")
def diagnostico(estacion: str, variable: Variable = "precipitacion", incluir_huecos: bool = False):
    _estacion(estacion)
    d = S()["diag"]
    r = d[(d.clave_estacion == estacion) & (d.variable == variable)]
    if r.empty:
        raise HTTPException(404, "Sin diagnóstico para esa estación y variable")
    out = _nn(r)[0]
    for k in ("faltantes_por_anio", "faltantes_por_mes"):
        out[k] = json.loads(out[k])
    if incluir_huecos:
        h = S()["huecos"]
        out["huecos"] = _nn(h[(h.clave_estacion == estacion) & (h.variable == variable)].drop(columns=["clave_estacion", "variable"]))
    return out



# ───────────────────────── mapa del día (para el monitor de SIGWEB) ─────────────────────────
class PuntoMapa(BaseModel):
    clave_estacion: str
    nombre: str
    municipio: str
    dependencia: str
    latitud: float
    longitud: float
    altitud: Optional[float] = None
    valor: Optional[float] = None
    limite_inferior: Optional[float] = None
    limite_superior: Optional[float] = None
    origen: Optional[str] = Field(None, description="observado | estimado | no_estimable | fuera_de_operacion")
    calidad: Optional[str] = None
    metodo: str = ""


class MapaOut(BaseModel):
    variable: Variable
    etiqueta: str
    unidad: str
    fecha: str
    fecha_min: str
    fecha_max: str
    nivel_intervalo: float
    estaciones: list[PuntoMapa]


@app.get(f"{V1}/mapa", tags=["series"], response_model=MapaOut,
         summary="Un día para todas las estaciones: valor, intervalo y bandera (lo que pinta el Monitor de Clima)")
def mapa(variable: Variable = "precipitacion", fecha: Optional[str] = Query(None, description="AAAA-MM-DD; por defecto el último día con datos")):
    gs = S()["groups"]
    lo = min(g.fecha.min() for (_, v), g in gs.items() if v == variable)
    hi = max(g.fecha.max() for (_, v), g in gs.items() if v == variable)
    try:
        f = pd.Timestamp(fecha) if fecha else hi
    except ValueError:
        raise HTTPException(422, "Fecha inválida: usa AAAA-MM-DD")
    if f < lo or f > hi:
        raise HTTPException(404, f"Sin datos para {f.date()}; el rango disponible es {lo.date()} a {hi.date()}")
    out = []
    for e in S()["est"].itertuples():
        g = gs.get((e.clave_estacion, variable))
        r = None if g is None else g[g.fecha == f]
        base = dict(clave_estacion=e.clave_estacion, nombre=str(e.nombre), municipio=str(e.municipio),
                    dependencia=str(getattr(e, "dependencia", "No especificada")), latitud=float(e.latitud),
                    longitud=float(e.longitud), altitud=None if pd.isna(e.altitud) else float(e.altitud))
        if r is None or r.empty or not bool(r.iloc[0].en_operacion):
            out.append(PuntoMapa(**base, origen="fuera_de_operacion"))
            continue
        r = r.iloc[0]
        nn = lambda x: None if pd.isna(x) else float(x)
        out.append(PuntoMapa(**base, valor=nn(r.valor), limite_inferior=nn(r.limite_inferior), limite_superior=nn(r.limite_superior),
                             origen=r.origen, calidad=r.calidad, metodo=r.metodo))
    return {"variable": variable, "etiqueta": NOMBRES[variable], "unidad": UNIDADES[variable], "fecha": f.strftime("%Y-%m-%d"),
            "fecha_min": lo.strftime("%Y-%m-%d"), "fecha_max": hi.strftime("%Y-%m-%d"),
            "nivel_intervalo": S()["metricas"]["protocolo"]["intervalo_nominal"], "estaciones": out}


# ───────────────────────── series ─────────────────────────
@app.get(f"{V1}/estaciones/{{estacion}}/series", tags=["series"], response_model=SerieOut,
         summary="Serie completada por estación y periodo (endpoint 2)")
def series(estacion: str, variable: Variable = "precipitacion",
           inicio: Optional[str] = Query(None, description="AAAA-MM-DD"),
           fin: Optional[str] = Query(None, description="AAAA-MM-DD"),
           limite: int = Query(2000, ge=1, le=20000), offset: int = Query(0, ge=0)):
    e = _estacion(estacion)
    g = S()["groups"].get((estacion, variable))
    if g is None:
        raise HTTPException(404, "Sin serie para esa estación y variable")
    try:
        if inicio:
            g = g[g.fecha >= pd.Timestamp(inicio)]
        if fin:
            g = g[g.fecha <= pd.Timestamp(fin)]
    except ValueError:
        raise HTTPException(422, "Fecha inválida: usa AAAA-MM-DD")
    g = g[g.en_operacion.astype(bool)]
    total = len(g)
    cut = g.iloc[offset:offset + limite]
    pts = [{"fecha": r.fecha.strftime("%Y-%m-%d"), "valor": None if pd.isna(r.valor) else float(r.valor),
            "limite_inferior": None if pd.isna(r.limite_inferior) else float(r.limite_inferior),
            "limite_superior": None if pd.isna(r.limite_superior) else float(r.limite_superior),
            "origen": r.origen, "calidad": r.calidad, "metodo": r.metodo, "motivo_no_estimable": r.motivo_no_estimable,
            "longitud_hueco": int(r.longitud_hueco), "vecinos_disponibles": int(r.vecinos_disponibles),
            "sospechoso": bool(r.sospechoso)} for r in cut.itertuples()]
    nxt = offset + limite
    return {"clave_estacion": estacion, "municipio": e.municipio, "variable": variable, "etiqueta": NOMBRES[variable],
            "unidad": UNIDADES[variable], "nivel_intervalo": S()["metricas"]["protocolo"]["intervalo_nominal"],
            "conteos": {k: int(v) for k, v in g.calidad.value_counts().items()}, "total": total, "offset": offset,
            "devueltos": len(pts), "siguiente_offset": nxt if nxt < total else None, "puntos": pts}


def _traza(estacion, fecha, variable):
    g = S()["groups"].get((estacion, variable))
    try:
        f = pd.Timestamp(fecha)
    except ValueError:
        raise HTTPException(422, "Fecha inválida: usa AAAA-MM-DD")
    r = g[g.fecha == f] if g is not None else None
    if r is None or r.empty:
        raise HTTPException(404, "No hay registro para esa fecha")
    r = r.iloc[0]
    vec = next((x["vecinos"] for x in S()["vecinos"][variable] if x["clave_estacion"] == estacion), [])
    usadas = []
    for v in vec:
        gg = S()["groups"][(v["clave_estacion"], variable)]
        q = gg[(gg.fecha == f) & (gg.origen == "observado")]
        if len(q):
            usadas.append(dict(v, valor_observado=float(q.valor.iloc[0])))
    return {"fecha": fecha, "origen": r.origen, "calidad": r.calidad, "metodo": r.metodo,
            "motivo_no_estimable": r.motivo_no_estimable, "longitud_hueco": int(r.longitud_hueco),
            "vecinas_con_dato_ese_dia": usadas,
            "nota": "Evidencia disponible ese día (vecinas con dato observado); no son contribuciones causales de cada vecina. "
                    "Un dato observado no fue imputado."}


@app.get(f"{V1}/estaciones/{{estacion}}/series/{{fecha}}/trazabilidad", tags=["series"], response_model=TrazabilidadOut,
         summary="Pasaporte del dato: vecinas observadas ese día")
def trazabilidad(estacion: str, fecha: str, variable: Variable = "precipitacion"):
    _estacion(estacion)
    return _traza(estacion, fecha, variable)


# ───────────────────────── modelo ─────────────────────────
@app.get(f"{V1}/metricas", tags=["modelo"], summary="Métricas de validación (endpoint 3)")
def metricas(variable: Optional[Variable] = None, estacion: Optional[str] = None):
    m = S()["metricas"]
    if estacion:
        _estacion(estacion)
        vs = [variable] if variable else list(m["variables"])
        return {"clave_estacion": estacion, "nota": "Validación con huecos artificiales en el bloque de evaluación.",
                "variables": {v: m["variables"][v].get("por_estacion", {}).get(estacion) for v in vs}}
    if variable:
        return {**{k: v for k, v in m.items() if k != "variables"}, "variables": {variable: m["variables"][variable]}}
    return m


@app.get(f"{V1}/recomendacion", tags=["modelo"], response_model=RecomendacionOut,
         summary="Método recomendado según la longitud del hueco")
def recomendacion(variable: Variable, longitud_hueco: int = Query(..., ge=1)):
    v = S()["metricas"]["variables"][variable]
    rec, b = v.get("recomendacion_por_hueco", {}), _rango(longitud_hueco)
    if b not in rec:
        raise HTTPException(404, "No hay validación suficiente para ese tamaño de hueco")
    gt = "corto" if longitud_hueco <= 3 else "medio" if longitud_hueco <= 30 else "largo"
    return {"variable": variable, "longitud_hueco": longitud_hueco, "rango": b, **rec[b],
            "metodo_activo_en_produccion": v["metodos_por_hueco"][gt],
            "nota": "ml = gradient boosting; vecinos = anomalía/razón normal de vecinas; lineal = interpolación (≤30 d); "
                    "ens = mezcla ml+vecinos; idw = distancia inversa. MAE medido en huecos artificiales."}


@app.get(f"{V1}/homogeneidad", tags=["modelo"], response_model=list[Homogeneidad],
         summary="Posibles cambios abruptos en las series (SNHT) — temperatura y precipitación")
def homogeneidad(estacion: Optional[str] = None, variable: Optional[Variable] = None, solo_cambios: bool = False):
    h = S()["hom"]
    if h.empty:
        return []
    if estacion:
        h = h[h.clave_estacion == estacion]
    if variable:
        h = h[h.variable == variable]
    if solo_cambios:
        h = h[h.posible_cambio]
    return _nn(h)


@app.get(f"{V1}/descargas/{{archivo}}", tags=["meta"], summary="Descargar archivos de resultados")
def descargar(archivo: Literal["predicciones.csv", "diagnostico.csv", "series_completadas.csv", "metricas.json",
                               "homogeneidad.csv", "huecos.csv"]):
    if not (OUT / archivo).exists():
        raise HTTPException(404, "Ese archivo no se generó (¿sin test_publico.csv?)")
    return FileResponse(OUT / archivo, filename=archivo)


# ───────────────────────── asistente (determinista, anclado a los datos) ─────────────────────────
def _norm(t):
    return unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode()


def _entender(q, estacion, variable, fecha):
    n = _norm(q)
    ests = S()["est"]
    if not estacion:
        m = re.search(r"sim\s?-?(\d{1,4})", n)
        if m:
            cand = f"SIM{int(m.group(1)):03d}"
            estacion = cand if (ests.clave_estacion == cand).any() else None
        if not estacion:   # clave literal en la pregunta (p. ej. CLICOM 11096)
            for k in ests.clave_estacion.astype(str):
                if re.search(rf"(?<![\w-]){re.escape(k.lower())}(?![\w-])", n):
                    estacion = k
                    break
        if not estacion:
            for r in ests.itertuples():
                if _norm(r.municipio) in n or _norm(r.nombre) in n:
                    estacion = r.clave_estacion
                    break
    if not variable:
        if re.search(r"lluvia|precip|pluvi", n):
            variable = "precipitacion"
        elif re.search(r"minima|tmin", n):
            variable = "tmin"
        elif re.search(r"maxima|tmax|temperatura|calor", n):
            variable = "tmax"
    if not fecha:
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", n)
        d = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", n)
        fecha = m.group(0) if m else (f"{d.group(3)}-{int(d.group(2)):02d}-{int(d.group(1)):02d}" if d else None)
    if re.search(r"vecin|respald|soport|apoy", n):
        intent = "vecinas"
    elif re.search(r"cuando no|no debo|no usar|no confi|evitar|cuidado", n):
        intent = "cuando_no_usar"
    elif re.search(r"cambio|homogene|snht|sensor|abrupt|salto", n):
        intent = "homogeneidad"
    elif re.search(r"metodo|recomend|que modelo|interpol", n):
        intent = "recomendacion"
    elif re.search(r"error|metric|mae|rmse|precision|cobertura|confiable es el modelo|que tan bueno", n):
        intent = "metricas"
    elif re.search(r"complet|faltan|vacio|hueco|dato.*(falt|disponible)|porcentaje", n):
        intent = "completitud"
    else:
        intent = "desconocida"
    return intent, estacion, variable, fecha


@app.post(f"{V1}/asistente", tags=["asistente"], response_model=RespuestaAsistente,
          summary="Asistente: responde preguntas sobre una estación usando solo los resultados del pipeline")
def asistente(p: PreguntaIn):
    r = _asistente_reglas(p)
    base = r["respuesta"]
    out = llm.redactar(p.pregunta, r)
    return {**r, **out, "respuesta_base": base}


def _asistente_reglas(p: PreguntaIn):
    intent, est, var, fecha = _entender(p.pregunta, p.clave_estacion, p.variable, p.fecha)
    var_ = var or "precipitacion"
    if intent == "desconocida":
        return {"intencion": intent, "fuentes": [], "datos": {},
                "respuesta": "Puedo responder: qué tan completa está una estación, cuándo NO usar sus datos rellenados, "
                             "qué vecinas respaldan la estimación de un día (indica la fecha AAAA-MM-DD), si tiene cambios "
                             "abruptos, qué método conviene según el hueco y qué tan confiable es el modelo. "
                             "Menciona la estación (p. ej. SIM001) y, si aplica, la variable."}
    if intent in ("metricas", "recomendacion") and not est:
        m = S()["metricas"]["variables"][var_]["modelo"]
        if intent == "metricas":
            return {"intencion": intent, "fuentes": [f"{V1}/metricas?variable={var_}"], "datos": m,
                    "respuesta": f"{NOMBRES[var_]} (validación global con huecos artificiales): MAE {m['mae']:.2f} {UNIDADES[var_]}, "
                                 f"RMSE {m['rmse']:.2f}, sesgo {m['sesgo']:+.2f}, KGE {m['kge']:.2f}, cobertura del intervalo "
                                 f"{m['cobertura'] * 100:.0f} % (nominal 90 %)."}
    if not est:
        return {"intencion": intent, "fuentes": [], "datos": {},
                "respuesta": "¿De qué estación hablamos? Indica la clave (p. ej. SIM001) o el municipio."}
    e = _estacion(est)
    f_diag = f"{V1}/estaciones/{est}/diagnostico?variable={var_}"
    d = S()["diag"]
    d = d[(d.clave_estacion == est) & (d.variable == var_)].iloc[0]
    g = S()["groups"][(est, var_)]
    g = g[g.en_operacion.astype(bool)]
    cuenta = g.calidad.value_counts().to_dict()
    cam = (est, var_) in _cambios()

    if intent == "completitud":
        txt = (f"La estación {est} ({e.municipio}) tiene {d.porcentaje_vacios:.1f} % de vacíos en {NOMBRES[var_].lower()} "
               f"({int(d.faltantes)} de {int(d.esperados)} días esperados). Huecos: {int(d.cortos)} cortos (1–3 d), "
               f"{int(d.medios)} medios (4–30 d) y {int(d.largos)} largos (>30 d); el más largo dura {int(d.hueco_mas_largo)} días.")
        return {"intencion": intent, "fuentes": [f_diag], "datos": {"diagnostico": _nn(pd.DataFrame([d]))[0]}, "respuesta": txt}

    if intent == "cuando_no_usar":
        razones = []
        ne = int(cuenta.get("no_estimable", 0))
        baja = int(cuenta.get("estimado_baja", 0))
        if ne:
            razones.append(f"{ne} días quedaron como no_estimable (sin información suficiente): no hay dato que usar.")
        if baja:
            razones.append(f"{baja} días estimados tienen baja confianza (pocas vecinas, hueco largo o intervalo ancho): úselos solo como orientación.")
        if d.hueco_mas_largo > 30:
            razones.append(f"Hay huecos de hasta {int(d.hueco_mas_largo)} días: la precisión se degrada en huecos largos (ver /metricas).")
        if cam:
            razones.append("Se detectó un posible cambio abrupto en la serie: ningún estimado se considera de alta confianza.")
        if d.sospechosos:
            razones.append(f"{int(d.sospechosos)} valores observados fueron marcados como sospechosos por el control de calidad.")
        txt = f"Para {est} ({NOMBRES[var_].lower()}) conviene NO usar sin cuidado: " + (" ".join(razones) if razones else "no se detectó ninguna restricción especial; aun así, los estimados nunca equivalen a mediciones.")
        return {"intencion": intent, "fuentes": [f_diag, f"{V1}/estaciones/{est}/series?variable={var_}", f"{V1}/homogeneidad?estacion={est}"],
                "datos": {"conteo_calidad": {k: int(v) for k, v in cuenta.items()}, "cambio_abrupto": cam}, "respuesta": txt}

    if intent == "homogeneidad":
        h = S()["hom"]
        h = h[(h.clave_estacion == est)] if not h.empty else h
        if h.empty:
            return {"intencion": intent, "fuentes": [f"{V1}/homogeneidad"], "datos": {}, "respuesta": f"No hay análisis de homogeneidad para {est}."}
        partes = []
        for r in h.itertuples():
            partes.append(f"{NOMBRES[r.variable]}: " + (f"posible cambio hacia {r.fecha_probable} (salto {r.salto_medio:+.2f} {r.unidad_salto})" if r.posible_cambio else "sin cambio abrupto detectado"))
        return {"intencion": intent, "fuentes": [f"{V1}/homogeneidad?estacion={est}"], "datos": {"homogeneidad": _nn(h)},
                "respuesta": f"Prueba SNHT para {est}: " + "; ".join(partes) + ". Es una heurística, no un dictamen."}

    if intent == "vecinas":
        if not fecha:
            return {"intencion": intent, "fuentes": [], "datos": {}, "respuesta": "¿Para qué día? Indica la fecha en formato AAAA-MM-DD."}
        t = _traza(est, fecha, var_)
        nv = t["vecinas_con_dato_ese_dia"]
        if t["origen"] == "observado":
            txt = f"El {fecha} la estación {est} SÍ midió {NOMBRES[var_].lower()}: es un dato observado, no estimado."
        elif t["origen"] == "no_estimable":
            txt = f"El {fecha} no se estimó ({t['motivo_no_estimable'] or 'sin información suficiente'}). Vecinas con dato ese día: {len(nv)}."
        else:
            lista = ", ".join(f"{v['clave_estacion']} ({v['distancia_km']:.0f} km, r={v['correlacion']:.2f}, valor {v['valor_observado']:.1f} {UNIDADES[var_]})" for v in nv[:6])
            txt = (f"El {fecha} el dato de {est} fue estimado ({t['calidad']}, método {t['metodo']}, hueco de {t['longitud_hueco']} d). "
                   f"Vecinas con dato observado ese día: {lista or 'ninguna'}.")
        return {"intencion": intent, "fuentes": [f"{V1}/estaciones/{est}/series/{fecha}/trazabilidad?variable={var_}"], "datos": t, "respuesta": txt}

    if intent == "recomendacion":
        n = re.search(r"(\d+)\s*d", _norm(p.pregunta))
        L = int(n.group(1)) if n else int(d.hueco_mas_largo or 1)
        r = recomendacion(var_, max(L, 1))
        return {"intencion": intent, "fuentes": [f"{V1}/recomendacion?variable={var_}&longitud_hueco={L}"], "datos": r,
                "respuesta": f"Para un hueco de {L} días en {NOMBRES[var_].lower()}, el mejor método validado es '{r['mejor']}' "
                             f"(MAE {r['mae'].get(r['mejor'], float('nan')):.2f} {UNIDADES[var_]}); en producción se usa '{r['metodo_activo_en_produccion']}'."}

    # metricas de una estación
    m = S()["metricas"]["variables"][var_].get("por_estacion", {}).get(est)
    if not m:
        return {"intencion": intent, "fuentes": [], "datos": {}, "respuesta": f"No hay validación específica para {est}."}
    return {"intencion": intent, "fuentes": [f"{V1}/metricas?estacion={est}&variable={var_}"], "datos": m,
            "respuesta": f"En {est}, {NOMBRES[var_].lower()}: MAE {m['mae']:.2f} {UNIDADES[var_]}, sesgo {m['sesgo']:+.2f}, "
                         f"cobertura del intervalo {m['cobertura'] * 100:.0f} % (validación con huecos artificiales)."}


# ───────────────────────── front ─────────────────────────
WEB = ROOT / "web"
if WEB.exists():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
