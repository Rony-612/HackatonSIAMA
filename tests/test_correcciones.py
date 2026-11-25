"""Pruebas de los puntos señalados en la auditoría contra el reto."""
import json

import numpy as np
import pandas as pd

from gapfill.conformal import StratifiedConformal
from gapfill.config import Config
from gapfill.data import load_dataset
from gapfill.quality import QualityRule
from conftest import ROOT


def test_conformal_mondrian_lluvia_no_colapsa_a_cero_en_dias_humedos():
    rng = np.random.default_rng(1)
    n = 3000
    reg = np.where(rng.random(n) < 0.3, "humedo", "seco")
    score = np.where(reg == "seco", 0.0, rng.exponential(0.5, n))   # los días secos dan score 0 (como en lluvia real)
    c = StratifiedConformal(0.1, 40).fit(score, np.array(["largo"] * n), np.array(["lluvias"] * n), reg)
    q = c.lookup(np.array(["largo"]), np.array(["lluvias"]), np.array(["humedo"]))[0]
    assert q > 0.3, "en el régimen húmedo el ajuste debe ser positivo"
    assert c.lookup(np.array(["largo"]), np.array(["lluvias"]), np.array(["seco"]))[0] == 0.0


def test_calibracion_real_de_lluvia_tiene_q_positivo_en_humedo(outputs):
    t = json.loads((outputs / "metricas.json").read_text())["variables"]["precipitacion"]["calibracion"]
    assert any(v["q"] > 0 for k, v in t.items() if k.endswith("humedo")), t


def test_bandera_alta_existe_en_lluvia_con_pod_mayor_a_cero(outputs):
    f = json.loads((outputs / "metricas.json").read_text())["variables"]["precipitacion"]["banderas_validacion"]
    assert f["estimado_alta"]["n"] > 0 and f["estimado_alta"]["mae"] < f["estimado_baja"]["mae"]


def test_calidad_regla_usa_evidencia_y_alerta_snht():
    r = QualityRule().fit(np.arange(100.0), np.arange(100.0), np.array(["*"] * 100))
    w, n, s, g, reg = np.array([1.0, 1.0]), np.array([5, 5]), np.array([90.0, 90.0]), np.array([2, 2]), np.array(["*", "*"])
    out = r.apply(w, n, s, g, reg, flagged=np.array([False, True]))
    assert list(out) == ["estimado_alta", "estimado_baja"]
    assert r.apply(w, np.array([1, 1]), s, g, reg)[0] == "estimado_baja"   # < 3 vecinas


def test_tmax_siempre_mayor_que_tmin_en_dias_estimados(outputs):
    s = pd.read_csv(outputs / "series_completadas.csv", parse_dates=["fecha"])
    t = s[s.variable.isin(["tmax", "tmin"])].pivot_table(index=["clave_estacion", "fecha"], columns="variable", values=["valor", "origen"], aggfunc="first")
    ok = t[("valor", "tmax")].notna() & t[("valor", "tmin")].notna()
    est = ok & ((t[("origen", "tmax")] == "estimado") | (t[("origen", "tmin")] == "estimado"))
    assert (t.loc[est, ("valor", "tmax")].astype(float) > t.loc[est, ("valor", "tmin")].astype(float)).all()


def test_intervalo_de_temperatura_respeta_el_observado_complementario(outputs):
    s = pd.read_csv(outputs / "series_completadas.csv", parse_dates=["fecha"])
    a = s[(s.variable == "tmax") & (s.origen == "estimado")][["clave_estacion", "fecha", "limite_inferior"]]
    b = s[(s.variable == "tmin") & (s.origen == "observado")][["clave_estacion", "fecha", "valor"]]
    m = a.merge(b, on=["clave_estacion", "fecha"])
    assert (m.limite_inferior > m.valor).all()


def test_qc_descarta_atipicos_severos_y_codigos_de_faltante():
    ds = load_dataset(ROOT / "data", Config())
    assert (ds.invalid["precipitacion"] >= ds.suspicious["precipitacion"] & np.isnan(ds.values["precipitacion"])).all()
    assert ds.qc_log["tmax_codigos_de_faltante"] >= 0
    # el paquete sintético no trae atípicos: se inyectan sensor pegado y un pico imposible y se verifica que se descartan
    from gapfill.data import _atypical
    dates = pd.date_range("2021-01-01", periods=120)
    x = (20 + 3 * np.sin(np.arange(120) / 9)).reshape(-1, 1)
    x[40:50] = 31.0          # 10 días idénticos
    x[80] = 5.0              # pico de -17 °C contra la mediana móvil
    sus, sev = _atypical("tmax", x, dates, Config())
    assert sev[41:50].all() and sev[80, 0]


def test_diagnostico_cuenta_los_objetivos_publicos_como_huecos_reales():
    ds = load_dataset(ROOT / "data", Config())
    assert ds.diag_values is not None
    keys = ds.stations.clave_estacion.tolist()
    for r in ds.public.head(50).itertuples():
        assert np.isnan(ds.diag_values[r.variable][ds.dates.get_loc(r.fecha), keys.index(r.clave_estacion)])


def test_operacion_se_infiere_si_faltan_columnas(tmp_path):
    import shutil
    shutil.copytree(ROOT / "data", tmp_path / "d")
    st = pd.read_csv(tmp_path / "d" / "estaciones.csv").drop(columns=["inicio_operacion", "fin_operacion"])
    st.to_csv(tmp_path / "d" / "estaciones.csv", index=False)
    ds = load_dataset(tmp_path / "d", Config())
    assert ds.qc_log["operacion_inferida_de_datos"] is True and ds.active.any()


def test_test_publico_es_opcional(tmp_path):
    import shutil
    shutil.copytree(ROOT / "data", tmp_path / "d")
    (tmp_path / "d" / "test_publico.csv").unlink()
    ds = load_dataset(tmp_path / "d", Config())
    assert len(ds.public) == 0


def test_asistente_responde_y_cita_fuente(outputs):
    import os, importlib
    os.environ["SIAMA_OUTPUT_DIR"] = str(outputs)
    import app.main as m
    importlib.reload(m)
    from fastapi.testclient import TestClient
    c = TestClient(m.app)
    r = c.post("/api/v1/asistente", json={"pregunta": "¿Qué tan completa está la estación SIM001?"}).json()
    assert r["intencion"] == "completitud" and "SIM001" in r["respuesta"] and r["fuentes"]
    r = c.post("/api/v1/asistente", json={"pregunta": "¿Qué vecinas respaldan 2024-07-10 en SIM001 en tmax?"}).json()
    assert r["intencion"] == "vecinas"
    assert c.post("/api/v1/asistente", json={"pregunta": "ab"}).status_code == 422
    assert c.get("/api/v1/estaciones/SIM001/series?limite=5&offset=5").json()["offset"] == 5
    assert c.get("/api/v1/metricas?estacion=SIM001").status_code == 200
