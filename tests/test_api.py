import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(outputs):
    os.environ["SIAMA_OUTPUT_DIR"] = str(outputs)
    import importlib
    import app.main as m
    importlib.reload(m)
    return TestClient(m.app)


def test_tres_endpoints_obligatorios(client):
    d = client.get("/api/v1/estaciones/SIM001/diagnostico?variable=tmax").json()
    assert {"porcentaje_vacios", "cortos", "medios", "largos", "faltantes_por_anio"} <= d.keys()
    s = client.get("/api/v1/estaciones/SIM001/series?variable=tmax&inicio=2024-07-01&fin=2024-07-31").json()
    assert s["total"] == 31 and all("origen" in p and "calidad" in p for p in s["puntos"])
    assert "variables" in client.get("/api/v1/metricas").json()


def test_errores_claros(client):
    assert client.get("/api/v1/estaciones/XXX/series").status_code == 404
    assert client.get("/api/v1/estaciones/SIM001/series?inicio=hoy").status_code == 422
    assert client.get("/api/v1/estaciones/SIM001/series?variable=viento").status_code == 422


def test_estimados_traen_intervalo_y_no_estimables_no_traen_valor(client):
    pts = client.get("/api/v1/estaciones/SIM002/series?variable=precipitacion&limite=10000").json()["puntos"]
    for p in pts:
        if p["origen"] == "estimado":
            assert p["limite_inferior"] is not None and p["limite_inferior"] <= p["valor"] <= p["limite_superior"]
        if p["origen"] == "no_estimable":
            assert p["valor"] is None and p["motivo_no_estimable"]


def test_widget_y_docs(client):
    assert client.get("/").status_code == 200 and client.get("/openapi.json").status_code == 200
    assert client.get("/api/v1/proyecto").json()["nombre"]


def test_mapa_del_dia(client):
    c = client
    d = c.get("/api/v1/mapa?variable=tmax").json()
    assert d["fecha"] == d["fecha_max"] and len(d["estaciones"]) >= 2
    p = d["estaciones"][0]
    assert {"clave_estacion", "valor", "calidad", "dependencia", "latitud", "longitud"} <= set(p)
    f = d["fecha_min"]
    assert c.get(f"/api/v1/mapa?variable=precipitacion&fecha={f}").json()["fecha"] == f
    assert c.get("/api/v1/mapa?fecha=1900-01-01").status_code == 404
    assert c.get("/api/v1/mapa?fecha=no").status_code == 422
