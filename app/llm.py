"""Capa opcional de IBM Granite para el asistente.

Principio de diseño: Granite NUNCA calcula ni estima nada. Solo redacta, en lenguaje natural, una respuesta
a partir de datos que el pipeline ya produjo (contexto JSON). Después se verifica que toda cifra del texto
generado exista en ese contexto; si no, se descarta y se responde con el texto determinista por reglas.

Proveedores (variable GRANITE_PROVIDER):
  watsonx : IBM watsonx.ai (nube). Requiere WATSONX_APIKEY, WATSONX_PROJECT_ID (y opcional WATSONX_URL, WATSONX_MODEL_ID).
  ollama  : Granite de pesos abiertos en local (sin internet ni llaves). OLLAMA_HOST, OLLAMA_MODEL.
  none    : sin LLM (solo reglas).
  auto    : (por defecto) watsonx si hay credenciales; si no, ollama si OLLAMA_MODEL está definido; si no, none.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

SYSTEM = (
    "Eres el asistente de calidad de datos climatológicos del SIAMA (Guanajuato). Responde en español, claro y breve "
    "(máximo 4 oraciones), para un ciudadano o un técnico. Usa EXCLUSIVAMENTE los datos del bloque CONTEXTO. "
    "Prohibido inventar o calcular cifras nuevas: copia las cifras tal como aparecen. Si el contexto no alcanza "
    "para responder, dilo. Recuerda que un valor estimado nunca es una medición."
)

_NUM = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)?")


def proveedor() -> str:
    p = os.getenv("GRANITE_PROVIDER", "auto").lower()
    if p != "auto":
        return p if p in ("watsonx", "ollama") else "none"
    if os.getenv("WATSONX_APIKEY") and os.getenv("WATSONX_PROJECT_ID"):
        return "watsonx"
    if os.getenv("OLLAMA_MODEL"):
        return "ollama"
    return "none"


def etiqueta() -> str:
    p = proveedor()
    if p == "watsonx":
        return f"IBM Granite vía watsonx.ai ({os.getenv('WATSONX_MODEL_ID', 'ibm/granite-3-8b-instruct')})"
    if p == "ollama":
        return f"IBM Granite local ({os.getenv('OLLAMA_MODEL')})"
    return "reglas deterministas"


# ───────── llamadas a los proveedores (aisladas para poder probarlas con un doble) ─────────
def _chat_watsonx(messages: list[dict]) -> str:
    from ibm_watsonx_ai import Credentials                       # import perezoso: dependencia opcional
    from ibm_watsonx_ai.foundation_models import ModelInference

    cred = Credentials(url=os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com"), api_key=os.environ["WATSONX_APIKEY"])
    modelo = ModelInference(model_id=os.getenv("WATSONX_MODEL_ID", "ibm/granite-3-8b-instruct"), credentials=cred,
                            project_id=os.environ["WATSONX_PROJECT_ID"],
                            params={"temperature": 0, "max_tokens": 350})
    return modelo.chat(messages=messages)["choices"][0]["message"]["content"]


def _chat_ollama(messages: list[dict]) -> str:
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    body = json.dumps({"model": os.environ["OLLAMA_MODEL"], "messages": messages, "stream": False,
                       "options": {"temperature": 0, "num_predict": 350}}).encode()
    req = urllib.request.Request(f"{host}/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=float(os.getenv("GRANITE_TIMEOUT", "25"))) as r:
        return json.loads(r.read())["message"]["content"]


def _chat(messages: list[dict]) -> str:
    return _chat_watsonx(messages) if proveedor() == "watsonx" else _chat_ollama(messages)


# ───────── verificación anti-alucinación ─────────
def _numeros(texto: str) -> list[float]:
    out = []
    for m in _NUM.findall(texto):
        try:
            out.append(float(m.replace(",", ".")))
        except ValueError:
            pass
    return out


def cifras_respaldadas(generado: str, contexto: str) -> tuple[bool, list[float]]:
    """Cada número del texto generado debe aparecer en el contexto (igual o redondeado), o ser su %, o ser un entero pequeño."""
    ctx = _numeros(contexto)
    ctx_all = ctx + [c * 100 for c in ctx]
    malos = []
    for x in _numeros(generado):
        if abs(x) <= 3 and float(x).is_integer():          # "3 vecinas", "1 día": conteos triviales
            continue
        dec = len(str(x).split(".")[1]) if "." in str(x) and not float(x).is_integer() else 0
        tol = 0.5 * 10 ** (-dec) + 1e-9
        if not any(abs(x - c) <= tol for c in ctx_all):
            malos.append(x)
    return (not malos), malos


def redactar(pregunta: str, resultado: dict) -> dict:
    """Devuelve {'respuesta', 'generado_por', 'verificacion'}. Nunca lanza: ante cualquier fallo cae a las reglas."""
    base = resultado["respuesta"]
    fallback = {"respuesta": base, "generado_por": "reglas deterministas", "verificacion": "no aplica"}
    if proveedor() == "none" or resultado.get("intencion") == "desconocida":
        return fallback
    contexto = json.dumps({"respuesta_base": base, "datos": resultado.get("datos", {}), "fuentes": resultado.get("fuentes", [])},
                          ensure_ascii=False, default=str)
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"CONTEXTO:\n{contexto}\n\nPREGUNTA: {pregunta}"}]
    try:
        texto = _chat(msgs).strip()
    except Exception as e:                                   # sin red, sin llave, modelo ausente, timeout…
        return {**fallback, "verificacion": f"proveedor no disponible ({type(e).__name__}); se usó el texto por reglas"}
    ok, malos = cifras_respaldadas(texto, contexto)
    if not texto or not ok:
        return {**fallback, "verificacion": f"rechazada: cifras sin respaldo {malos[:5]}; se usó el texto por reglas"}
    return {"respuesta": texto, "generado_por": etiqueta(), "verificacion": "cifras verificadas contra el contexto"}
