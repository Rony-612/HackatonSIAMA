"""Pruebas de las piezas que más se rompen: huecos, fugas de información, intervalos."""
import numpy as np
import pandas as pd

from gapfill.config import Config
from gapfill.conformal import StratifiedConformal, conformal_quantile
from gapfill.features import FeatureSet, build_features, make_ctx
from gapfill.gaps import gap_lengths, gap_type
from gapfill.masks import block_mask, coverage_mask
from gapfill.neighbors import climatology, select_neighbors


def test_gap_lengths_y_tipos():
    x = np.array([[0, 1, 1, 1, 0, 1, 0, 1, 1, 1, 1]], bool).T   # 1 = día faltante: rachas de 3, 1 y 4 días
    g = gap_lengths(x)[:, 0]
    assert list(g) == [0, 3, 3, 3, 0, 1, 0, 4, 4, 4, 4]
    assert list(gap_type([1, 3, 4, 30, 31, 90])) == ["corto", "corto", "medio", "medio", "largo", "largo"]


def test_conformal_cubre_90_en_datos_nuevos():
    rng = np.random.default_rng(1)
    cal, test = rng.normal(size=4000), rng.normal(size=20000)
    q = conformal_quantile(np.abs(cal), 0.10)
    assert abs((np.abs(test) <= q).mean() - 0.90) < 0.02


def test_conformal_por_estrato_usa_respaldo_si_faltan_muestras():
    s = np.r_[np.random.default_rng(0).normal(size=300), [50.0] * 3]
    g = np.r_[["corto"] * 300, ["largo"] * 3]
    c = StratifiedConformal(0.1, min_n=40).fit(np.abs(s), g, np.array(["lluvias"] * 303))
    assert c.lookup(np.array(["largo"]), np.array(["lluvias"]))[0] == c.q[("*", "*", "*")]   # 3 muestras < 40 -> global


def _toy(T=800, S=5, seed=0):
    rng = np.random.default_rng(seed)
    doy = (np.arange(T) % 365) + 1
    shared = np.convolve(rng.normal(0, 2, T), np.ones(5) / 5, "same")
    a = 18 + 5 * np.sin(2 * np.pi * doy / 365)[:, None] + shared[:, None] + rng.normal(0, .7, (T, S))
    return a, doy


def test_climatologia_no_mira_fuera_de_la_ventana():
    a, doy = _toy()
    win = np.zeros(len(a), bool)
    win[:400] = True
    c1 = climatology(a, doy, win, 7)
    b = a.copy()
    b[400:] += 100          # cambiar el "futuro" no debe cambiar la normal
    assert np.allclose(c1, climatology(b, doy, win, 7))


def test_vecinas_no_usan_datos_fuera_del_entrenamiento():
    a, doy = _toy()
    st = pd.DataFrame({"latitud": np.linspace(20, 21, 5), "longitud": np.linspace(-101, -100.5, 5), "altitud": [1800] * 5})
    win = np.zeros(len(a), bool)
    win[:400] = True
    cfg = Config()
    clim = climatology(a, doy, win, 7)
    n1 = select_neighbors("tmax", st, a, clim, win, cfg)
    b = a.copy()
    b[400:] = np.random.default_rng(9).normal(size=b[400:].shape)    # destruir la correlación en el futuro
    n2 = select_neighbors("tmax", st, b, clim, win, cfg)
    assert np.allclose(n1.corr, n2.corr, equal_nan=True)


def test_bloques_solo_sobre_datos_observados_y_consecutivos():
    a, _ = _toy()
    a[100:110, 0] = np.nan
    m = block_mask(a, 0, 800, [2, 8, 35], 1, np.random.default_rng(3))
    assert not (m & np.isnan(a)).any()                    # nunca se "esconde" un dato que ya faltaba
    for j in range(a.shape[1]):
        edges = np.flatnonzero(np.diff(np.r_[False, m[:, j], False].astype(int))).reshape(-1, 2)
        assert all((e - s) in (2, 8, 35) for s, e in edges)


def test_features_de_entrenamiento_no_contienen_el_objetivo():
    """En una fila enmascarada, el dato propio más cercano está al menos a 1 día: nunca es el propio objetivo."""
    a, doy = _toy()
    st = pd.DataFrame({"latitud": np.linspace(20, 21, 5), "longitud": np.linspace(-101, -100.5, 5), "altitud": [1800] * 5})
    win = np.ones(len(a), bool)
    cfg = Config()
    clim = climatology(a, doy, win, 7)
    nt = select_neighbors("tmax", st, a, clim, win, cfg)

    class DS:
        dates = pd.date_range("2020-01-01", periods=len(a))
        stations = st
        basin = None
    ctx = make_ctx("tmax", DS, clim)
    m = coverage_mask(a, 0, 800, (2, 5, 10), 0.2, np.random.default_rng(4))
    am = a.copy()
    am[m] = np.nan
    fs = FeatureSet.from_mask(build_features(am, ctx, nt), m, ctx)
    assert (fs["dias_prev"] >= 1).all() and (fs["dias_next"] >= 1).all()
    assert (fs["long_hueco"] >= 2).all()


def test_homogeneidad_detecta_salto_real_y_no_marca_ruido():
    from gapfill.homogeneity import detect
    rng = np.random.default_rng(5)
    idx = pd.date_range("2020-01-01", periods=60, freq="MS")
    ruido = pd.Series(rng.normal(0, 0.3, 60), index=idx)
    assert detect(ruido)[3] is False
    saltado = ruido.copy()
    saltado.iloc[35:] += 2.0
    t, k, jump, hit = detect(saltado)
    assert hit and abs(k - 35) <= 2 and abs(jump - 2.0) < 0.3
    pequeno = ruido.copy()
    pequeno.iloc[35:] -= 0.3            # salto chico (típico de una vecina contaminada): no se marca
    assert detect(pequeno)[3] is False
