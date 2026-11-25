"""Orquestador: de series_diarias.csv a series completadas con intervalos, banderas y métricas.

Para cada variable:
  1. Se parte el tiempo en 4 bloques cronológicos: entrenamiento | selección | calibración | evaluación.
  2. Vecinas y normales climáticas se calculan SOLO con el bloque de entrenamiento (sin fugas).
  3. Se esconden bloques consecutivos de datos reales (huecos artificiales) en cada bloque y se construyen los
     features sobre la matriz ya enmascarada.
  4. El modelo se entrena en el bloque de entrenamiento y se afina en selección (umbral de lluvia,
     peso del ensamble, método ganador por tipo de hueco).
  5. Los intervalos se calibran (conformal) en el bloque de calibración, por tipo de hueco x temporada x régimen.
  6. Todo se mide en el bloque de evaluación, que nadie usó para decidir nada.
  7. Con las decisiones ya fijas se REENTRENA con todo el histórico y se rellenan los huecos reales.
"""
import json
from pathlib import Path

from dataclasses import replace

import numpy as np
import pandas as pd

from .conformal import StratifiedConformal
from .config import RAIN_MM, TEMPS, VARS, Config
from .data import load_dataset
from .features import FeatureSet, build_features, make_ctx
from .gaps import diagnose, gap_bucket
from .homogeneity import homogeneity_table
from .masks import block_mask, coverage_mask
from .metrics import by_group, score
from .neighbors import climatology, select_neighbors
from .quality import QualityRule
from .validate import validate_outputs


def _clean(o):
    """JSON no admite NaN/inf: se publican como null."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return float(o) if np.isfinite(o) else None
    if isinstance(o, np.integer):
        return int(o)
    return o


def _t0(ds):
    """Primer día en que operan al menos 2 estaciones (o 1 si la red es de una). Antes de eso no hay vecinas con quién
    aprender, así que las particiones entrenamiento/selección/calibración/evaluación se cuentan desde aquí."""
    need = min(2, len(ds.stations))
    idx = np.flatnonzero(ds.active.sum(axis=1) >= need)
    return int(idx[0]) if len(idx) else 0


def _bounds(T, cfg, t0=0):
    a, b, c = (t0 + int((T - t0) * x) for x in cfg.split)
    return (t0, a), (a, b), (b, c), (c, T)


def adapt_config(cfg, ds):
    """Una red pequeña no puede cumplir umbrales pensados para una red grande: se ajustan al máximo de vecinas posibles
    (siempre queda registrado en el reporte). Con 2 estaciones cada una tiene como máximo 1 vecina."""
    nmax = max(len(ds.stations) - 1, 1)
    ch = {}
    for k in ("min_neighbors_estimate", "min_neighbors_alta"):
        if getattr(cfg, k) > nmax:
            ch[k] = nmax
    return (replace(cfg, **ch) if ch else cfg), ch


def _clim(var, a, doy, window):
    return climatology(a, doy, window, 7 if var in TEMPS else 15)


def _train_rows(var, a, ctx, nt, lo, hi, cfg, rng):
    parts, ys = [], []
    for _ in range(cfg.train_rounds):
        m = coverage_mask(a, lo, hi, cfg.train_lengths, cfg.train_coverage, rng)
        am = a.copy()
        am[m] = np.nan
        fs = FeatureSet.from_mask(build_features(am, ctx, nt), m, ctx)
        keep = fs.estimable(cfg)
        parts.append(_subset(fs, keep))
        ys.append(a[m][keep])
    return FeatureSet.concat(parts), np.concatenate(ys)


def _subset(fs, keep):
    return FeatureSet({k: v[keep] for k, v in fs.cols.items()}, fs.t[keep], fs.j[keep], fs.months[keep])


def _reps(length):
    """Repeticiones por longitud: los huecos cortos se repiten más para que pesen en la evaluación (~30 días por longitud)."""
    return int(np.clip(round(30 / length), 1, 12))


def _eval_rows(var, a, ctx, nt, bounds, cfg, rng):
    """Selección, calibración y evaluación: huecos de varias longitudes (incluye 60 y 90 días)."""
    lengths = sorted(cfg.eval_lengths + cfg.stress_lengths, reverse=True)
    out = {"sel": ([], []), "cal": ([], []), "ev": ([], [])}
    for r in range(cfg.eval_rounds):
        # cada ronda esconde solo ~1/rondas de las longitudes: así las vecinas casi siempre tienen dato, como en la realidad
        lens = lengths[r::cfg.eval_rounds]
        masks = {k: block_mask(a, lo, hi, lens, _reps, rng) for k, (lo, hi) in zip(("sel", "cal", "ev"), bounds)}
        am = a.copy()
        for m in masks.values():
            am[m] = np.nan
        feat = build_features(am, ctx, nt)
        for k, m in masks.items():
            fs = FeatureSet.from_mask(feat, m, ctx)
            out[k][0].append(fs)
            out[k][1].append(a[m])
    return {k: (FeatureSet.concat(v[0]), np.concatenate(v[1])) for k, v in out.items()}


def _route(choices, fs, cands):
    pt, meth = np.full(len(fs), np.nan), np.full(len(fs), "ml", dtype=object)
    for g, name in choices.items():
        m = (fs.gap_type == g) & np.isfinite(cands[name])
        pt[m], meth[m] = cands[name][m], name
    fb = ~np.isfinite(pt) & np.isfinite(cands["ml"])
    pt[fb] = cands["ml"][fb]
    return pt, meth


def _choose(fs, y, cands, cfg):
    """Por tipo de hueco, el método con menor MAE en el bloque de selección (solo entre los que existen en ese tipo)."""
    ok_all = fs.estimable(cfg)
    choices, table = {}, {}
    for g in ("corto", "medio", "largo"):
        base = ok_all & (fs.gap_type == g)
        names = [n for n in cands if base.sum() and np.isfinite(cands[n][base]).mean() >= 0.95]
        rows = base.copy()
        for n in names:
            rows &= np.isfinite(cands[n])
        if rows.sum() < 25 or len(names) < 2:
            choices[g], table[g] = "ml", {}
            continue
        mae = {n: float(np.abs(cands[n][rows] - y[rows]).mean()) for n in names}
        choices[g], table[g] = min(mae, key=mae.get), {**{n: round(v, 4) for n, v in mae.items()}, "n": int(rows.sum())}
    return choices, table


def _tf(var):
    """Escala en la que se mide el error conformal: log1p en lluvia (el error crece con la intensidad), lineal en temperatura."""
    return (np.log1p, np.expm1) if var == "precipitacion" else (lambda x: x, lambda x: x)


def _scale(pred):
    """Anchura que el modelo cuantílico asigna al día (semiancho). Sirve para normalizar el error en temperatura."""
    return np.maximum(pred["hi"] - pred["lo"], 0.5) / 2


def _interval(fs, pred, point, conf, var, regime):
    # el estrato de calibración es el rango fino del hueco (1-3 / 4-30 / 31-60 / 61+): el error de una estación no crece igual en todos
    q = conf.lookup(gap_bucket(fs["long_hueco"]), fs.season, regime)
    if var in TEMPS:
        # conformal dividido NORMALIZADO sobre el punto que se publica (venga de ML, de la vecina o de la interpolación):
        # intervalo = punto ± q·semiancho_del_modelo; así la cobertura se garantiza para el valor final, no para el ML.
        half = q * _scale(pred)
        return point - half, point + half
    f, finv = _tf(var)
    lo, hi = finv(f(np.maximum(pred["lo"], 0)) - q), finv(f(pred["hi"]) + q)
    lo, hi = np.minimum(lo, point), np.maximum(hi, point)
    lo, hi, point = np.maximum(lo, 0), np.maximum(hi, 0), np.maximum(point, 0)
    return lo, hi


def _conf_scores(var, y, pred, point):
    if var in TEMPS:
        return np.abs(y - point) / _scale(pred)
    f, _ = _tf(var)
    lo = np.maximum(pred["lo"], 0)
    return np.maximum(f(lo) - f(y), f(y) - f(pred["hi"]))


def process_variable(var, ds, spec, cfg: Config, rng):
    a, T, S = ds.values[var], len(ds.dates), len(ds.stations)
    doy = ds.dates.dayofyear.to_numpy()
    tr, sel_b, cal_b, ev_b = _bounds(T, cfg, _t0(ds))
    win = np.zeros(T, bool)
    win[tr[0]:tr[1]] = True

    clim = _clim(var, a, doy, win)
    nt = select_neighbors(var, ds.stations, a, clim, win, cfg, ds.basin)
    ctx = make_ctx(var, ds, clim)

    fs_tr, y_tr = _train_rows(var, a, ctx, nt, tr[0], tr[1], cfg, rng)
    if len(fs_tr) < 100:
        raise ValueError(f"Entrenamiento insuficiente para {var}: {len(fs_tr)} filas")
    rows = _eval_rows(var, a, ctx, nt, (sel_b, cal_b, ev_b), cfg, rng)

    model = spec.build_model(var, cfg)
    model.fit(fs_tr, y_tr)
    fs_cal, y_cal = rows["cal"]
    # Ajuste (umbral de lluvia, pesos del ensamble) y elección del método por tipo de hueco: con las ventanas de selección Y
    # calibración. Las series derivan con los años (el mejor método de hace 20 años puede no serlo hoy), y la ventana de
    # evaluación, que nunca se toca aquí, es la que mide el resultado honesto.
    fs_sel, y_sel = FeatureSet.concat([rows["sel"][0], fs_cal]), np.concatenate([rows["sel"][1], y_cal])
    p_sel = model.predict(fs_sel)
    model.tune(fs_sel, y_sel, p_sel)
    p_sel = model.predict(fs_sel)
    cand_sel = spec.candidates(var, model, fs_sel, p_sel)
    choices, router_table = _choose(fs_sel, y_sel, cand_sel, cfg)

    p_cal = model.predict(fs_cal)
    pt_cal, _ = _route(choices, fs_cal, spec.candidates(var, model, fs_cal, p_cal))
    ok = fs_cal.estimable(cfg) & np.isfinite(pt_cal)
    reg_cal = model.regime(p_cal)
    conf = StratifiedConformal(cfg.alpha, cfg.min_stratum).fit(
        _conf_scores(var, y_cal, p_cal, pt_cal)[ok], gap_bucket(fs_cal["long_hueco"])[ok], fs_cal.season[ok], reg_cal[ok])
    lo_c, hi_c = _interval(fs_cal, p_cal, pt_cal, conf, var, reg_cal)
    qual = QualityRule(min_neighbors=cfg.min_neighbors_alta).fit((hi_c - lo_c)[ok], fs_cal["suma_corr"][ok], reg_cal[ok])

    # ---------- evaluación ----------
    fs_ev, y_ev = rows["ev"]
    p_ev = model.predict(fs_ev)
    cand_ev = spec.candidates(var, model, fs_ev, p_ev)
    pt_ev, meth_ev = _route(choices, fs_ev, cand_ev)
    reg_ev = model.regime(p_ev)
    lo_ev, hi_ev = _interval(fs_ev, p_ev, pt_ev, conf, var, reg_ev)
    est = fs_ev.estimable(cfg) & np.isfinite(pt_ev)
    pt_m = np.where(est, pt_ev, np.nan)
    flag_ev = qual.apply(hi_ev - lo_ev, fs_ev["n_vecinos"], fs_ev["suma_corr"], fs_ev["long_hueco"], reg_ev)
    bucket = gap_bucket(fs_ev["long_hueco"])
    common = est & np.isfinite(fs_ev["idw"])

    # Calibración de PRODUCCIÓN: el error de la ventana más reciente es el mejor indicio del error futuro (las estaciones
    # derivan: p. ej. Tmin entre dos estaciones de Salamanca cambió ~5 °C en 40 años), así que los intervalos con los que
    # se rellenan huecos reales se calibran con calibración + evaluación. Las métricas de arriba siguen siendo
    # honestas: se midieron con el calibrador que NO vio la ventana de evaluación.
    sc_f = np.concatenate([_conf_scores(var, y_cal, p_cal, pt_cal)[ok], _conf_scores(var, y_ev, p_ev, pt_ev)[est]])
    bk_f = np.concatenate([gap_bucket(fs_cal["long_hueco"])[ok], bucket[est]])
    se_f = np.concatenate([fs_cal.season[ok], fs_ev.season[est]])
    rg_f = np.concatenate([reg_cal[ok], reg_ev[est]])
    conf_prod = StratifiedConformal(cfg.alpha, cfg.min_stratum).fit(sc_f, bk_f, se_f, rg_f)
    w_f = np.concatenate([(lambda l, h: (h - l)[ok])(*_interval(fs_cal, p_cal, pt_cal, conf_prod, var, reg_cal)),
                          (lambda l, h: (h - l)[est])(*_interval(fs_ev, p_ev, pt_ev, conf_prod, var, reg_ev))])
    qual_prod = QualityRule(min_neighbors=cfg.min_neighbors_alta).fit(
        w_f, np.concatenate([fs_cal["suma_corr"][ok], fs_ev["suma_corr"][est]]), rg_f)

    rep = {
        "modelo": score(var, y_ev, pt_m, lo_ev, hi_ev),
        "linea_base_idw": score(var, y_ev, fs_ev["idw"]),
        "linea_base_vecinos": score(var, y_ev, fs_ev["est_vecinos"]),
        "comparacion_comun": {
            "n": int(common.sum()),
            "modelo": score(var, y_ev[common], pt_ev[common]),
            "idw": score(var, y_ev[common], fs_ev["idw"][common]),
            "vecinos": score(var, y_ev[common], fs_ev["est_vecinos"][common]),
        },
        "por_tipo_hueco": by_group(var, y_ev, pt_m, lo_ev, hi_ev, fs_ev.gap_type),
        "por_rango_hueco": by_group(var, y_ev, pt_m, lo_ev, hi_ev, bucket),
        "por_temporada": by_group(var, y_ev, pt_m, lo_ev, hi_ev, fs_ev.season),
        "por_estacion": by_group(var, y_ev, pt_m, lo_ev, hi_ev, np.array([ds.stations.clave_estacion[j] for j in fs_ev.j])),
        "metodos_por_hueco": choices,
        "seleccion_mae_por_metodo": router_table,
        "calibracion": conf.table(),
        "calibracion_produccion": conf_prod.table(),
        "umbral_ancho_calidad": qual.thr,
        "afinado": model.tuning,
    }
    strata = {}
    for g in ("corto", "medio", "largo"):
        for s in ("lluvias", "estiaje"):
            m = est & (fs_ev.gap_type == g) & (fs_ev.season == s)
            if m.sum() >= 15:
                strata[f"{g}|{s}"] = score(var, y_ev[m], pt_ev[m], lo_ev[m], hi_ev[m])
    rep["cobertura_por_estrato"] = strata
    rep["banderas_validacion"] = {f: score(var, y_ev[est & (flag_ev == f)], pt_ev[est & (flag_ev == f)],
                                           lo_ev[est & (flag_ev == f)], hi_ev[est & (flag_ev == f)])
                                  for f in ("estimado_alta", "estimado_baja")}
    rec = {}
    for b in ("1-3", "4-30", "31-60", "61+"):
        m = est & (bucket == b) & np.isfinite(fs_ev["idw"])
        usable = {n: v for n, v in cand_ev.items() if m.sum() and np.isfinite(v[m]).all()}   # p. ej. 'lineal' no existe en >30 d
        if m.sum() >= 10:
            maes = {n: float(np.abs(v[m] - y_ev[m]).mean()) for n, v in usable.items()}
            maes["idw"] = float(np.abs(fs_ev["idw"][m] - y_ev[m]).mean()) if np.isfinite(fs_ev["idw"][m]).all() else None
            clean = {k: v for k, v in maes.items() if v is not None}
            rec[b] = {"mejor": min(clean, key=clean.get), "mae": {k: round(v, 3) for k, v in clean.items()}, "n": int(m.sum())}
    rep["recomendacion_por_hueco"] = rec
    warn = []
    alta, baja = rep["banderas_validacion"]["estimado_alta"], rep["banderas_validacion"]["estimado_baja"]
    if alta.get("mae") is not None and baja.get("mae") is not None and alta["mae"] >= baja["mae"]:
        warn.append(f"{var}: la bandera 'alta confianza' NO tiene menos error que 'baja' (MAE {alta['mae']:.2f} vs {baja['mae']:.2f}).")
    cov = rep["modelo"].get("cobertura")
    if cov is not None and abs(cov - (1 - cfg.alpha)) > 0.05:
        warn.append(f"{var}: cobertura global {cov:.2f} se aleja del nominal {1 - cfg.alpha:.2f}.")
    rep["advertencias"] = warn

    # ---------- reentrenar con TODO el histórico y rellenar huecos reales ----------
    allw = np.ones(T, bool)
    clim_f = _clim(var, a, doy, allw)
    nt_f = select_neighbors(var, ds.stations, a, clim_f, allw, cfg, ds.basin)
    ctx_f = make_ctx(var, ds, clim_f)
    fs_f, y_f = _train_rows(var, a, ctx_f, nt_f, 0, T, cfg, rng)
    final = spec.build_model(var, cfg)
    final.fit(fs_f, y_f)
    final.set_tuning(model.tuning)
    miss = np.isnan(a) & ds.active
    fs_p = FeatureSet.from_mask(build_features(a, ctx_f, nt_f), miss, ctx_f)
    p_p = final.predict(fs_p)
    cand_p = spec.candidates(var, final, fs_p, p_p)
    pt_p, meth_p = _route(choices, fs_p, cand_p)
    reg_p = final.regime(p_p)
    lo_p, hi_p = _interval(fs_p, p_p, pt_p, conf_prod, var, reg_p)
    okp = fs_p.estimable(cfg) & np.isfinite(pt_p)
    reason = np.full(len(fs_p), "", dtype=object)
    reason[~okp & (fs_p["long_hueco"] > cfg.max_fill_days)] = "hueco_muy_largo"
    reason[~okp & (reason == "")] = "sin_vecinas_suficientes"
    prod = {"t": fs_p.t, "j": fs_p.j, "val": np.where(okp, pt_p, np.nan), "lo": np.where(okp, lo_p, np.nan),
            "hi": np.where(okp, hi_p, np.nan), "method": meth_p, "reason": reason,
            "gap": fs_p["long_hueco"].astype(int), "nn": fs_p["n_vecinos"].astype(int),
            "quality": qual_prod.apply(hi_p - lo_p, fs_p["n_vecinos"], fs_p["suma_corr"], fs_p["long_hueco"], reg_p)}
    rep["regimen_conformal"] = {r: {"n": int((reg_cal[ok] == r).sum())} for r in np.unique(reg_cal)}
    rep["vecinas_por_estacion"] = [int(len(i)) for i in nt_f.ids]
    return rep, prod, nt_f


def _repair_pairs(full, ds, cfg, T, S):
    """Coherencia física: en TODA salida, Tmax > Tmin (con rango diurno mínimo cfg.min_dtr).
    En vez de borrar la pareja, se corrige:
      * ambas estimadas y contradictorias -> se centran en su media y se separan min_dtr;
      * una observada y otra estimada      -> la estimada se acota contra la observada.
    Los intervalos se recortan contra el dato observado complementario y siempre contienen al valor corregido."""
    k = cfg.min_dtr
    ox, on = ds.values["tmax"], ds.values["tmin"]
    X, N = full["tmax"], full["tmin"]
    xe, ne = np.isnan(ox) & np.isfinite(X["val"]), np.isnan(on) & np.isfinite(N["val"])
    xv = np.where(np.isfinite(ox), ox, X["val"])
    nv = np.where(np.isfinite(on), on, N["val"])
    with np.errstate(invalid="ignore"):
        bad = np.isfinite(xv) & np.isfinite(nv) & ((xv - nv) < k)
    both = bad & xe & ne
    mid = (xv + nv) / 2
    X["val"][both], N["val"][both] = (mid + k / 2)[both], (mid - k / 2)[both]
    only_x = bad & xe & ~ne
    X["val"][only_x] = (nv + k)[only_x]
    only_n = bad & ne & ~xe
    N["val"][only_n] = (xv - k)[only_n]
    # recorte de intervalos contra el observado complementario
    with np.errstate(invalid="ignore"):
        lo_floor = np.where(np.isfinite(on), on + k, np.where(np.isfinite(N["val"]), N["val"] + k, -np.inf))
        hi_ceil = np.where(np.isfinite(ox), ox - k, np.where(np.isfinite(X["val"]), X["val"] - k, np.inf))
    X["lo"] = np.where(xe, np.maximum(X["lo"], np.minimum(lo_floor, X["val"])), X["lo"])
    N["hi"] = np.where(ne, np.minimum(N["hi"], np.maximum(hi_ceil, N["val"])), N["hi"])
    for D in (X, N):
        e = np.isfinite(D["val"])
        D["lo"] = np.where(e, np.minimum(D["lo"], D["val"]), D["lo"])
        D["hi"] = np.where(e, np.maximum(D["hi"], D["val"]), D["hi"])
    return int((bad & (xe | ne)).sum())


def run(data_dir, out_dir, spec, cfg: Config = Config(), log=print):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ds = load_dataset(data_dir, cfg)
    if len(ds.stations) < 2:
        raise ValueError("GeoTrust necesita al menos 2 estaciones con operación simultánea: sin ninguna vecina no hay "
                         "información con la que estimar, y rellenar con la climatología sería presentar un supuesto como dato. "
                         "Con una sola estación todos sus huecos serían 'no_estimable'.")
    cfg, ajustes = adapt_config(cfg, ds)
    if ajustes:
        log(f"Red de {len(ds.stations)} estaciones: umbrales ajustados {ajustes}")
    rng = np.random.default_rng(cfg.seed)
    T, S = len(ds.dates), len(ds.stations)
    reports, prods, nbrs, diag_rows, gap_rows = {}, {}, {}, [], []
    for v in VARS:
        log(f"[{spec.name}] {v}: entrenando y validando…")
        reports[v], prods[v], nbrs[v] = process_variable(v, ds, spec, cfg, rng)
        d, g = diagnose(ds, v)
        diag_rows += d
        gap_rows += g

    hom = homogeneity_table(ds, nbrs)
    flagged = {(r.clave_estacion, r.variable) for r in hom.itertuples() if r.posible_cambio}

    full = {}
    for v in VARS:
        P = prods[v]
        m = {k: np.full((T, S), np.nan) for k in ("val", "lo", "hi")}
        for k in m:
            m[k][P["t"], P["j"]] = P[k]
        full[v] = m
    n_fix = _repair_pairs(full, ds, cfg, T, S)

    rows = []
    for v in VARS:
        P, a = prods[v], ds.values[v]
        F = full[v]
        meth = np.full((T, S), "", dtype=object)
        qual = np.full((T, S), "", dtype=object)
        reason = np.full((T, S), "", dtype=object)
        gap = np.zeros((T, S), int)
        nn = np.zeros((T, S), int)
        meth[P["t"], P["j"]], qual[P["t"], P["j"]], reason[P["t"], P["j"]] = P["method"], P["quality"], P["reason"]
        gap[P["t"], P["j"]], nn[P["t"], P["j"]] = P["gap"], P["nn"]
        obs = np.isfinite(a)
        est = ~obs & np.isfinite(F["val"]) & ds.active
        for j, st in enumerate(ds.stations.itertuples()):
            origen = np.where(obs[:, j], "observado", np.where(est[:, j], "estimado", "no_estimable"))
            q_j = qual[:, j].copy()
            if (st.clave_estacion, v) in flagged:   # cambio abrupto detectado: ninguna estimación es de alta confianza
                q_j[q_j == "estimado_alta"] = "estimado_baja"
            calidad = np.where(obs[:, j], "observado", np.where(est[:, j], q_j, "no_estimable"))
            rows.append(pd.DataFrame({
                "clave_estacion": st.clave_estacion, "fecha": ds.dates, "variable": v,
                "valor_original": ds.raw[v][:, j],
                "valor": np.where(obs[:, j], a[:, j], np.where(est[:, j], F["val"][:, j], np.nan)),
                "valor_estimado": np.where(est[:, j], F["val"][:, j], np.nan),
                "limite_inferior": np.where(est[:, j], F["lo"][:, j], np.nan),
                "limite_superior": np.where(est[:, j], F["hi"][:, j], np.nan),
                "origen": origen, "calidad": calidad,
                "metodo": np.where(est[:, j], meth[:, j], ""),
                "motivo_no_estimable": np.where(origen == "no_estimable", np.where(ds.active[:, j], reason[:, j], "fuera_de_operacion"), ""),
                "longitud_hueco": gap[:, j], "vecinos_disponibles": nn[:, j],
                "invalido": ds.invalid[v][:, j], "sospechoso": ds.suspicious[v][:, j], "en_operacion": ds.active[:, j]}))
    series = pd.concat(rows, ignore_index=True)

    series.to_csv(out / "series_completadas.csv", index=False, date_format="%Y-%m-%d", float_format="%.4f")
    pd.DataFrame(diag_rows).to_csv(out / "diagnostico.csv", index=False)
    pd.DataFrame(gap_rows).to_csv(out / "huecos.csv", index=False)
    hom.to_csv(out / "homogeneidad.csv", index=False)
    ds.stations.to_csv(out / "estaciones.csv", index=False)
    if len(ds.public):
        pred = ds.public.merge(series, on=["clave_estacion", "fecha", "variable"], how="left", validate="one_to_one")
        pred[["clave_estacion", "fecha", "variable", "valor_estimado", "limite_inferior", "limite_superior",
              "origen", "calidad", "metodo", "motivo_no_estimable"]].to_csv(out / "predicciones.csv", index=False, date_format="%Y-%m-%d")
    keys = ds.stations.clave_estacion.tolist()
    prov = {v: [{"clave_estacion": keys[j], "vecinos": [
        {"clave_estacion": keys[k], "distancia_km": round(float(nbrs[v].dist[j, k]), 2),
         "correlacion": round(float(nbrs[v].corr[j, k]), 3),
         "diferencia_altitud_m": float(ds.stations.altitud[k] - ds.stations.altitud[j]),
         "misma_cuenca": None if ds.basin[j] < 0 or ds.basin[k] < 0 else bool(ds.basin[j] == ds.basin[k])} for k in ids]}
        for j, ids in enumerate(nbrs[v].ids)] for v in VARS}
    (out / "vecinos.json").write_text(json.dumps(prov, indent=1))
    warnings = [w for v in VARS for w in reports[v].pop("advertencias")]
    meta = {"proyecto": spec.meta(), "variables": reports, "qc": ds.qc_log, "advertencias": warnings,
            "pares_tmax_tmin_corregidos": n_fix,
            "estaciones_con_cambio_abrupto": sorted(f"{k}:{v}" for k, v in flagged),
            "protocolo": {"bloques": "40% entrenamiento / 20% selección / 20% calibración / 20% evaluación (cronológicos)",
                          "huecos_evaluacion_dias": sorted(cfg.eval_lengths + cfg.stress_lengths),
                          "rondas": cfg.eval_rounds, "semilla": cfg.seed, "intervalo_nominal": 1 - cfg.alpha,
                          "dia_lluvioso_mm": RAIN_MM, "reentrenado_con_todo_el_historico": True,
                          "calibracion_nota": "Las métricas usan intervalos calibrados con la ventana de calibración (que no vio la de evaluación). "
                                              "Para rellenar huecos reales los intervalos se recalibran con calibración + evaluación (el error más reciente) y se "
                                              "aplican al modelo reentrenado con todo el histórico (limitación conocida: no es validación cruzada CV+).",
                          "particiones_desde": str(ds.dates[_t0(ds)].date()), "estaciones_en_red": int(S),
                          "umbrales_ajustados_por_red_pequena": ajustes,
                          "nota": "Métricas sobre huecos artificiales del histórico disponible. No son el conjunto oculto del comité."}}
    (out / "metricas.json").write_text(json.dumps(_clean(meta), indent=1, allow_nan=False))
    (out / "proyecto.json").write_text(json.dumps(spec.meta(), indent=1, ensure_ascii=False))
    validate_outputs(out, data_dir if len(ds.public) else None)
    log(f"[{spec.name}] listo -> {out}")
    return meta
