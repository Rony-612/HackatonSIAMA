"""Modelo de GeoTrust SIAMA: Gradient Boosting + cuantiles + conformal CQR + trazabilidad.

  * HistGradientBoosting (maneja NaN de forma nativa) para la estimación puntual.
  * Temperatura: se modela la ANOMALÍA contra la normal de la estación (design.py).
  * Lluvia en dos etapas: clasificador de ocurrencia + cantidad en log1p con corrección de Duan (smearing), que
    evita el sesgo de "exp(E[log y]) < E[y]" al regresar a mm.
  * Cuantiles 5/95 entrenados por el propio modelo (en log1p para lluvia) -> el intervalo se ensancha donde hay duda;
    el engine los corrige con conformal CQR por estrato (tipo de hueco x temporada x régimen).
  * Pesos de entrenamiento inversos a la longitud del hueco: los huecos de 1-3 días no quedan ahogados por los de 90.
  * Candidatos para el enrutador por longitud de hueco: ml, vecinos (anomalía / razón normal), lineal (<= 30 d),
    ensamble ml+vecinos con peso afinado en el bloque de selección.
"""
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from gapfill.config import RAIN_MM, TEMPS
from gapfill.design import design

name = "geotrust"


def meta():
    return {"clave": name, "nombre": "GeoTrust SIAMA", "acento": "#1d4ed8",
            "lema": "Cada dato estimado con su pasaporte",
            "modelo": "HistGradientBoosting + cuantiles 5/95 + enrutador por longitud de hueco",
            "intervalos": "Conformal CQR de Mondrian (tipo de hueco x temporada x régimen húmedo/seco)",
            "distintivo": ["trazabilidad_vecinos", "homogeneidad", "asistente", "recomendacion_metodo"]}


def build_model(var, cfg):
    return GB(var, cfg)


def _weights(fs):
    """Peso inverso a sqrt(longitud del hueco), normalizado a media 1."""
    w = 1.0 / np.sqrt(np.maximum(fs["long_hueco"], 1.0))
    return w / w.mean()


class GB:
    def __init__(self, var, cfg):
        self.var, self.cfg = var, cfg
        self.temp = var in TEMPS
        self.tuning = {}
        self.smear = 1.0

    def _r(self, q=None):
        kw = dict(max_iter=140, learning_rate=0.07, max_leaf_nodes=24, min_samples_leaf=30,
                  l2_regularization=1.0, random_state=self.cfg.seed)
        if q is not None:
            kw.update(loss="quantile", quantile=q)
        return HistGradientBoostingRegressor(**kw)

    def fit(self, fs, y):
        X, base = design(self.var, fs)
        w = _weights(fs)
        if self.temp:
            t = y - base
            self.mean = self._r().fit(X, t, sample_weight=w)
            self.qlo = self._r(0.05).fit(X, t, sample_weight=w)
            self.qhi = self._r(0.95).fit(X, t, sample_weight=w)
        else:
            wet = y >= RAIN_MM
            self.clf = HistGradientBoostingClassifier(max_iter=140, learning_rate=0.07, max_leaf_nodes=24,
                                                      min_samples_leaf=30, l2_regularization=1.0,
                                                      random_state=self.cfg.seed).fit(X, wet.astype(int), sample_weight=w)
            ly = np.log1p(y)
            self.mean = self._r().fit(X[wet], ly[wet], sample_weight=w[wet])
            res = ly[wet] - self.mean.predict(X[wet])
            self.smear = float(np.mean(np.exp(res))) if self.cfg.smear else 1.0   # corrección de Duan
            # los cuantiles se calculan sobre log1p: al regresar con expm1 nunca salen negativos
            self.qlo, self.qhi = self._r(0.05).fit(X, ly, sample_weight=w), self._r(0.95).fit(X, ly, sample_weight=w)
        return self

    def predict(self, fs):
        X, base = design(self.var, fs)
        if self.temp:
            return {"point": base + self.mean.predict(X), "lo": base + self.qlo.predict(X), "hi": base + self.qhi.predict(X)}
        p_wet = self.clf.predict_proba(X)[:, 1]
        amt = np.maximum(np.exp(self.mean.predict(X)) * self.smear - 1.0, RAIN_MM)
        thr = self.tuning.get("umbral_lluvia", 0.5)
        point = np.where(p_wet >= thr, amt, 0.0)
        return {"point": point, "lo": np.maximum(np.expm1(self.qlo.predict(X)), 0),
                "hi": np.maximum(np.expm1(self.qhi.predict(X)), 0), "p_wet": p_wet, "amt": amt}

    def regime(self, pred):
        """Régimen predicho (solo lluvia): estrato de Mondrian y comparación justa de anchos."""
        if self.temp:
            return np.full(len(pred["point"]), "*")
        return np.where(pred["p_wet"] >= self.tuning.get("umbral_lluvia", 0.5), "humedo", "seco")

    def tune(self, fs, y, pred):
        ok = fs.estimable(self.cfg)
        if not self.temp:
            best = (-1, 0.5)
            wet = y[ok] >= RAIN_MM
            for thr in np.arange(0.25, 0.71, 0.05):
                pw = pred["p_wet"][ok] >= thr
                csi = (wet & pw).sum() / max(1, (wet | pw).sum())
                if csi > best[0]:
                    best = (csi, float(thr))
            self.tuning["umbral_lluvia"] = round(best[1], 2)
            pred = self.predict(fs)   # el punto depende del umbral
        # peso del ensamble ml + vecinos (MAE mínimo en el bloque de selección)
        okv = ok & np.isfinite(fs["est_vecinos"]) & np.isfinite(pred["point"])
        if okv.sum() >= 50:
            grid = np.linspace(0, 1, 11)
            mae = [np.abs(w * pred["point"][okv] + (1 - w) * fs["est_vecinos"][okv] - y[okv]).mean() for w in grid]
            self.tuning["w_ens"] = round(float(grid[int(np.argmin(mae))]), 2)
        else:
            self.tuning["w_ens"] = 1.0
        if self.temp:   # mismo ensamble, pero con la vecina corregida por el desfase local
            okl = ok & np.isfinite(fs["est_local"]) & np.isfinite(pred["point"])
            if okl.sum() >= 50:
                grid = np.linspace(0, 1, 11)
                mae = [np.abs(w * pred["point"][okl] + (1 - w) * fs["est_local"][okl] - y[okl]).mean() for w in grid]
                self.tuning["w_ens_local"] = round(float(grid[int(np.argmin(mae))]), 2)
            else:
                self.tuning["w_ens_local"] = 1.0

    def set_tuning(self, t):
        self.tuning = dict(t)


def candidates(var, model, fs, pred):
    """Métodos entre los que el enrutador elige por tipo de hueco (todos arrays alineados con fs)."""
    ml, vec = pred["point"], fs["est_vecinos"]
    w = model.tuning.get("w_ens", 1.0)
    lin = np.where(fs["long_hueco"] <= 30, fs["lineal"], np.nan)   # interpolar >30 días no es defendible
    if var == "precipitacion":
        lin = np.where(np.isfinite(lin), np.maximum(lin, 0), np.nan)
        vec = np.where(np.isfinite(vec), np.maximum(vec, 0), np.nan)
    ens = np.where(np.isfinite(vec), w * ml + (1 - w) * vec, ml)
    idw = fs["idw"] if var != "precipitacion" else np.where(np.isfinite(fs["idw"]), np.maximum(fs["idw"], 0), np.nan)
    out = {"ml": ml, "vecinos": vec, "lineal": lin, "ens": ens, "idw": idw}   # idw (la línea base) también compite en el enrutador
    if var != "precipitacion":   # vecina + desfase local interpolado a través del hueco
        loc = fs["est_local"]
        out["vecinos_local"] = loc
        wl = model.tuning.get("w_ens_local", 1.0)
        out["ens_local"] = np.where(np.isfinite(loc), wl * ml + (1 - wl) * loc, ml)
    return out
