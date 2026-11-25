"""Banderas de calidad derivadas de EVIDENCIA y verificadas contra el error real.

Una estimación es 'alta confianza' cuando el modelo tuvo un respaldo observacional fuerte ESE DÍA:
  * >= 3 vecinas observadas,
  * suma de correlaciones de las vecinas observadas por encima de la mediana de su régimen (seco/lluvioso en
    lluvia; todo en temperatura), medida en el bloque de calibración,
  * hueco de <= 30 días,
  * intervalo no más ancho que el percentil 75 de su régimen (el ancho se compara DENTRO del régimen: en lluvia
    un día seco previsto mide ~0 mm y uno lluvioso varios, no son comparables entre sí),
  * y la estación no tiene un cambio abrupto de homogeneidad (SNHT) en esa variable.
Después se comprueba, en el bloque de evaluación, que las 'alta' tengan menos error y cobertura parecida a las 'baja'.
"""
import numpy as np


class QualityRule:
    def __init__(self, min_neighbors=3, max_gap=30, width_pct=75):
        self.min_neighbors, self.max_gap, self.width_pct = min_neighbors, max_gap, width_pct
        self.w_thr, self.s_thr = {}, {}

    def fit(self, width, support, regime):
        regime = np.asarray(regime)
        for r in list(np.unique(regime)) + ["*"]:
            m = np.ones(len(width), bool) if r == "*" else regime == r
            if m.sum() < 15:
                m = np.ones(len(width), bool)
            self.w_thr[r] = float(np.percentile(width[m], self.width_pct))
            self.s_thr[r] = float(np.median(support[m]))
        return self

    @property
    def thr(self):
        return {"ancho_p75": self.w_thr, "suma_corr_mediana": self.s_thr}

    def apply(self, width, n_neighbors, support, gap_len, regime, flagged=None):
        regime = np.asarray(regime)
        w = np.array([self.w_thr.get(r, self.w_thr["*"]) for r in regime])
        s = np.array([self.s_thr.get(r, self.s_thr["*"]) for r in regime])
        good = (n_neighbors >= self.min_neighbors) & (support >= s) & (gap_len <= self.max_gap) & (width <= w)
        if flagged is not None:
            good &= ~flagged
        return np.where(good, "estimado_alta", "estimado_baja")
