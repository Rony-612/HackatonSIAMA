"""Calibración conformal dividida (split conformal) por estratos de Mondrian.

Idea: mides qué tanto falló el modelo en datos que NO vio (bloque de calibración) y usas el cuantil 90 % de esos
errores para ajustar el intervalo, de modo que cubra ~90 % en datos nuevos.  Se hace por estrato porque el error no
es igual en un hueco corto de estiaje que en uno largo de lluvias, ni en un día que se predice seco que en uno
que se predice lluvioso (en lluvia el régimen es la variable que más importa: sin él, los días secos, que son
mayoría, dejan el cuantil en 0 y el ajuste no hace nada).

Estrato = (tipo de hueco, temporada, régimen).  Si un estrato tiene menos de min_n muestras se usa el más general
que sí las tenga, SIN mezclar régimen: (g,s,r) -> (g,*,r) -> (*,*,r) -> (*,*,*).
"""
import numpy as np


def conformal_quantile(scores, alpha):
    s = np.sort(np.asarray(scores, float))
    n = len(s)
    k = int(np.ceil((n + 1) * (1 - alpha)))
    return float(s[min(k, n) - 1])


class StratifiedConformal:
    def __init__(self, alpha=0.10, min_n=40):
        self.alpha, self.min_n = alpha, min_n
        self.q, self.n = {}, {}

    def _put(self, key, scores):
        self.q[key], self.n[key] = conformal_quantile(scores, self.alpha), int(len(scores))

    def fit(self, scores, gap_type, season, regime=None):
        scores = np.asarray(scores, float)
        regime = np.full(len(scores), "*") if regime is None else np.asarray(regime)
        for r in np.unique(regime):
            mr = regime == r
            for g in np.unique(gap_type):
                for s in np.unique(season):
                    m = mr & (gap_type == g) & (season == s)
                    if m.sum() >= self.min_n:
                        self._put((g, s, r), scores[m])
                m = mr & (gap_type == g)
                if m.sum() >= self.min_n:
                    self._put((g, "*", r), scores[m])
            if mr.sum() >= 5:
                self._put(("*", "*", r), scores[mr])
        self._put(("*", "*", "*"), scores)
        return self

    def lookup(self, gap_type, season, regime=None):
        regime = np.full(len(gap_type), "*") if regime is None else np.asarray(regime)
        out = np.empty(len(gap_type))
        for i, (g, s, r) in enumerate(zip(gap_type, season, regime)):
            for key in ((g, s, r), (g, "*", r), ("*", "*", r), ("*", "*", "*")):
                if key in self.q:
                    out[i] = self.q[key]
                    break
        return out

    def table(self):
        return {"|".join(k): {"q": round(q, 4), "n": self.n[k]} for k, q in self.q.items()}
