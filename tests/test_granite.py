"""Capa IBM Granite: se prueba con un doble del proveedor (no hay red ni credenciales en CI)."""
import importlib
import os

from fastapi.testclient import TestClient

from app import llm


def _cliente(outputs, monkeypatch, chat):
    os.environ["SIAMA_OUTPUT_DIR"] = str(outputs)
    monkeypatch.setenv("GRANITE_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "granite-falso")
    import app.main as m
    importlib.reload(m)
    monkeypatch.setattr(llm, "_chat", chat)
    return TestClient(m.app)


P = {"pregunta": "¿Qué tan completa está la estación SIM001?"}


def test_sin_proveedor_usa_reglas(outputs, monkeypatch):
    monkeypatch.setenv("GRANITE_PROVIDER", "none")
    os.environ["SIAMA_OUTPUT_DIR"] = str(outputs)
    import app.main as m
    importlib.reload(m)
    r = TestClient(m.app).post("/api/v1/asistente", json=P).json()
    assert r["generado_por"] == "reglas deterministas" and "SIM001" in r["respuesta"]


def test_granite_redacta_si_las_cifras_existen(outputs, monkeypatch):
    def chat(msgs):                       # el doble reutiliza solo cifras del contexto
        ctx = msgs[1]["content"]
        return "La estación SIM001 " + ctx.split("tiene ")[1].split(" de ")[0] + " de sus días sin dato."
    r = _cliente(outputs, monkeypatch, chat).post("/api/v1/asistente", json=P).json()
    assert r["generado_por"].startswith("IBM Granite") and r["verificacion"].startswith("cifras verificadas")
    assert r["respuesta_base"] and r["respuesta"] != r["respuesta_base"]


def test_cifra_inventada_se_rechaza(outputs, monkeypatch):
    r = _cliente(outputs, monkeypatch, lambda msgs: "La estación SIM001 tiene 87.31 % de vacíos.").post("/api/v1/asistente", json=P).json()
    assert r["generado_por"] == "reglas deterministas" and r["verificacion"].startswith("rechazada")
    assert r["respuesta"] == r["respuesta_base"]


def test_proveedor_caido_no_rompe(outputs, monkeypatch):
    def chat(msgs):
        raise ConnectionError("sin red")
    r = _cliente(outputs, monkeypatch, chat).post("/api/v1/asistente", json=P).json()
    assert r["generado_por"] == "reglas deterministas" and "no disponible" in r["verificacion"]


def test_verificador_acepta_redondeo_y_porcentaje():
    ok, _ = llm.cifras_respaldadas("MAE 1.5 y cobertura 96 %", '{"mae": 1.537, "cobertura": 0.958}')
    assert ok
    ok, malos = llm.cifras_respaldadas("MAE 9.9", '{"mae": 1.537}')
    assert not ok and malos == [9.9]
