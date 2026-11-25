"""Pruebas sobre los resultados reales del pipeline (corre el pipeline una vez)."""
import json
import shutil

import pandas as pd
import pytest

from gapfill.validate import validate_outputs
from conftest import ROOT


def test_salida_cumple_el_contrato(outputs):
    validate_outputs(outputs, ROOT / "data")


def test_un_estimado_nunca_se_presenta_como_observado(outputs):
    s = pd.read_csv(outputs / "series_completadas.csv")
    assert (s[s.origen == "estimado"].calidad.str.startswith("estimado")).all()
    assert (s[s.origen == "observado"].calidad == "observado").all()


def test_predicciones_cubren_exactamente_el_test_publico(outputs):
    pub = pd.read_csv(ROOT / "data" / "test_publico.csv")
    pred = pd.read_csv(outputs / "predicciones.csv")
    assert len(pred) == len(pub) and not pred.duplicated(["clave_estacion", "fecha", "variable"]).any()
    est = pred[pred.origen == "estimado"]
    assert (est.limite_inferior <= est.valor_estimado).all() and (est.valor_estimado <= est.limite_superior).all()


def test_ml_mejora_a_la_linea_base_en_temperatura(outputs):
    m = json.loads((outputs / "metricas.json").read_text())["variables"]
    for v in ("tmax", "tmin"):
        c = m[v]["comparacion_comun"]
        assert c["modelo"]["mae"] < c["idw"]["mae"], f"{v}: el modelo no supera a IDW"


def test_cobertura_razonable_y_sin_estratos_descalibrados(outputs):
    m = json.loads((outputs / "metricas.json").read_text())["variables"]
    for v, x in m.items():
        assert 0.84 <= x["modelo"]["cobertura"] <= 0.97, f"{v}: cobertura {x['modelo']['cobertura']}"


def test_validacion_rechaza_un_observado_con_intervalo(outputs, tmp_path):
    bad = tmp_path / "bad"
    shutil.copytree(outputs, bad)
    s = pd.read_csv(bad / "series_completadas.csv")
    i = s.index[s.origen == "observado"][0]
    s.loc[i, "limite_superior"] = 99.0
    s.to_csv(bad / "series_completadas.csv", index=False)
    with pytest.raises(ValueError, match="observados con intervalo"):
        validate_outputs(bad)
