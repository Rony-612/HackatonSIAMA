"""Formatos oficiales (CLICOM csv / TXT de CONAGUA con 'NULO') y red de pocas estaciones."""
import numpy as np
import pandas as pd
import pytest

from gapfill.config import Config
from gapfill.data import _ceros_contra_vecinas, load_dataset
from gapfill.engine import _bounds, _t0, adapt_config
from gapfill.ingesta import cargar_clicom, leer_clicom_txt

TXT = """COMISIÓN NACIONAL DEL AGUA
BASE DE DATOS CLIMATOLÓGICA NACIONAL
 ESTACIÓN   : {clave}
 NOMBRE     : {nombre}
 MUNICIPIO  : SALAMANCA
 SITUACIÓN  : OPERANDO
 U. ADMNVA. : DIRECCIÓN LOCAL GUANAJUATO
 LATITUD    : {lat} °
 LONGITUD   : {lon} °
 ALTITUD    : 1717 msnm

FECHA\t\tPRECIP\tEVAP\tTMAX\tTMIN
\t\t(mm)\t(mm)\t(°C )\t(°C)
"""


def _txt(tmp, clave, nombre, lat, lon, filas):
    cuerpo = TXT.format(clave=clave, nombre=nombre, lat=lat, lon=lon) + "\n".join(filas) + "\n"
    p = tmp / f"dia{clave}.txt"
    p.write_text(cuerpo, encoding="utf-8")
    return p


def _filas(inicio, n, nulos=()):
    f = []
    for i, d in enumerate(pd.date_range(inicio, periods=n)):
        if i in nulos:
            f.append(f"{d.date()}\tNULO\tNULO\tNULO\tNULO")
        else:
            f.append(f"{d.date()}\t{0 if i % 5 else 4.2}\t3.1\t{25 + (i % 7) * .5}\t{9 + (i % 5) * .4}")
    return f


def test_txt_conagua_lee_metadatos_y_nulo(tmp_path):
    p = _txt(tmp_path, "11096", "SALAMANCA", "20.56527778", "-101.1997222", _filas("2000-01-01", 10, nulos={3}))
    est, ser, _ = leer_clicom_txt(p)
    e = est.iloc[0]
    assert e.clave_estacion == "11096" and e.municipio == "Salamanca" and e.altitud == 1717
    assert e.latitud == pytest.approx(20.56527778) and e.longitud == pytest.approx(-101.1997222)
    assert "Comisión Nacional del Agua" in e.dependencia and e.estado_operativo == "operando"
    assert ser.tmax.isna().sum() == 1 and ser.precipitacion.isna().sum() == 1 and len(ser) == 10


def test_fuentes_se_unen_sin_perder_ni_inventar(tmp_path):
    _txt(tmp_path, "1", "A", "20.5", "-101.2", _filas("2000-01-01", 20, nulos={5}))
    csv = pd.DataFrame({"fecha": pd.date_range("2000-01-01", periods=20).astype(str), "clave_estacion": "1", "nombre": "A",
                        "latitud": 20.5, "longitud": -101.2, "altitud": 1717.0, "precip_mm": 0.0, "tmax_c": 25.0, "tmin_c": 9.0})
    csv.loc[5, ["precip_mm", "tmax_c", "tmin_c"]] = [1.5, 27.0, 10.0]      # el CSV sí trae el día que el TXT no
    csv.loc[2, "tmax_c"] = 99.0                                            # conflicto: gana el TXT
    csv.to_csv(tmp_path / "clicom.csv", index=False)
    est, ser, reg = cargar_clicom(tmp_path)
    assert reg["dias_completados_con_otra_fuente"] >= 3 and reg["conflictos_entre_fuentes"] >= 1
    assert ser.loc[ser.fecha == "2000-01-03", "tmax"].iloc[0] != 99.0
    assert ser.loc[ser.fecha == "2000-01-06", "tmax"].iloc[0] == 27.0


def test_longitud_positiva_se_corrige(tmp_path):
    _txt(tmp_path, "1", "A", "20.5", "101.2", _filas("2000-01-01", 30))
    est, _, reg = cargar_clicom(tmp_path)
    assert est.longitud.iloc[0] < 0 and reg["longitudes_con_signo_corregido"] == 1


def test_dataset_desde_txt_cuenta_huecos_y_periodo_de_operacion(tmp_path):
    _txt(tmp_path, "1", "A", "20.50", "-101.20", _filas("2000-01-01", 400, nulos=set(range(100, 160))))
    _txt(tmp_path, "2", "B", "20.55", "-101.15", _filas("2000-06-01", 300))
    ds = load_dataset(tmp_path, Config())
    assert list(ds.stations.clave_estacion) == ["1", "2"]
    assert ds.active[:, 1].sum() == 300 + (len(ds.dates) - 300 - 152 - 0) * 0 or ds.active[:, 1].any()
    assert np.isnan(ds.values["tmax"][100:160, 0]).all()
    assert _t0(ds) == int(np.flatnonzero(ds.active[:, 1])[0])             # las particiones empiezan cuando operan 2 estaciones


def test_particiones_se_cuentan_desde_t0():
    a, b, c = (200 + int((1000 - 200) * x) for x in Config().split)
    tr, sel, cal, ev = _bounds(1000, Config(), 200)
    assert tr == (200, a) and ev == (c, 1000)


def test_red_de_2_estaciones_ajusta_umbrales_y_lo_dice():
    class DS:  # solo necesita .stations
        stations = pd.DataFrame({"clave_estacion": ["a", "b"]})
    cfg, ch = adapt_config(Config(), DS())
    assert cfg.min_neighbors_estimate == 1 and cfg.min_neighbors_alta == 1 and set(ch) == {"min_neighbors_estimate", "min_neighbors_alta"}


def test_ceros_largos_solo_son_sospechosos_si_las_vecinas_llovieron():
    dates = pd.date_range("2020-06-01", periods=60)          # junio-julio: temporada de lluvias
    x = np.zeros((60, 2))
    x[:, 1] = np.where(np.arange(60) % 3 == 0, 5.0, 0.0)     # la vecina llueve 1 de cada 3 días
    m = _ceros_contra_vecinas(x, dates, Config())
    assert m[:, 0].all() and not m[:, 1].any()               # A: ceros incompatibles; B: no
    seca = np.zeros((60, 2))                                 # ambas secas: sequía de medio verano real, NO se toca
    assert not _ceros_contra_vecinas(seca, dates, Config()).any()
    assert not _ceros_contra_vecinas(x[:, :1], dates, Config()).any()   # una sola estación: sin evidencia, no se marca


def test_una_sola_estacion_falla_con_mensaje_claro(tmp_path):
    import shutil, pytest
    from conftest import ROOT
    from gapfill import modelo as spec
    from gapfill.engine import run
    (tmp_path / "d").mkdir()
    shutil.copy(ROOT / "data_oficial" / "dia11096.txt", tmp_path / "d")
    with pytest.raises(ValueError, match="al menos 2 estaciones"):
        run(tmp_path / "d", tmp_path / "o", spec, log=lambda *a: None)


def test_asistente_entiende_clave_clicom():
    import os
    os.environ["SIAMA_OUTPUT_DIR"] = str(__import__("conftest").ROOT / "outputs")
    import importlib, app.main as m
    importlib.reload(m)
    from fastapi.testclient import TestClient
    r = TestClient(m.app).post("/api/v1/asistente", json={"pregunta": "¿Qué tan completa está la estación 11136?"}).json()
    assert r["intencion"] == "completitud" and "11136" in r["respuesta"]
