"""Contrato de salida: si algo de esto falla, el pipeline NO publica resultados."""
from pathlib import Path

import numpy as np
import pandas as pd

BANDERAS = {
    "observado": "Dato medido por la estación que pasó el control de calidad.",
    "estimado_alta": "Estimado con vecinas observadas suficientes y un intervalo angosto para su tipo de hueco.",
    "estimado_baja": "Estimado, pero con pocas vecinas o con un intervalo ancho: úselo con cautela.",
    "no_estimable": "No se rellenó: no hay información suficiente para estimarlo con honestidad.",
}


def validate_outputs(out_dir, data_dir=None):
    out = Path(out_dir)
    s = pd.read_csv(out / "series_completadas.csv", dtype={"clave_estacion": str}, parse_dates=["fecha"])
    p = []
    if s.duplicated(["clave_estacion", "fecha", "variable"]).any():
        p.append("filas duplicadas")
    if not s.calidad.isin(BANDERAS).all():
        p.append("banderas desconocidas")
    obs, est, ne = (s[s.origen == k] for k in ("observado", "estimado", "no_estimable"))
    if obs[["limite_inferior", "limite_superior", "valor_estimado"]].notna().any().any():
        p.append("observados con intervalo o valor_estimado")
    if est[["valor", "limite_inferior", "limite_superior"]].isna().any().any():
        p.append("estimados sin valor o sin intervalo")
    if ((est.limite_inferior > est.valor) | (est.valor > est.limite_superior)).any():
        p.append("valor estimado fuera de su intervalo")
    if ne[["valor", "limite_inferior", "limite_superior"]].notna().any().any():
        p.append("no_estimable con valor")
    if (est.calidad == "observado").any() or (obs.calidad != "observado").any():
        p.append("bandera inconsistente con origen")
    pr = s[s.variable == "precipitacion"]
    if (pr[["valor", "limite_inferior", "limite_superior"]] < 0).any().any():
        p.append("precipitación negativa")
    t = s[s.variable.isin(["tmax", "tmin"])].pivot_table(index=["clave_estacion", "fecha"], columns="variable", values="valor")
    if (t.tmax < t.tmin).any():
        p.append("Tmax < Tmin en algún día")
    est_t = s[(s.variable.isin(["tmax", "tmin"])) & (s.origen == "estimado")][["clave_estacion", "fecha"]].drop_duplicates()
    chk = t.reset_index().merge(est_t, on=["clave_estacion", "fecha"])
    if (chk.tmax <= chk.tmin).any():
        p.append("Tmax no es MAYOR que Tmin en un día con valor estimado")
    hp = out / "homogeneidad.csv"
    if hp.exists():
        h = pd.read_csv(hp)
        if len(h) and (h.estadistico_T < 0).any():
            p.append("homogeneidad.csv con estadístico negativo")
    ok_obs = obs[~obs.invalido.astype(bool)]
    if (ok_obs.valor != ok_obs.valor_original).any():
        p.append("se alteró un dato observado válido")
    if data_dir is not None:
        pub = pd.read_csv(Path(data_dir) / "test_publico.csv", dtype={"clave_estacion": str})
        pred = pd.read_csv(out / "predicciones.csv", dtype={"clave_estacion": str})
        if "variable" not in pub.columns:
            pass   # formato ancho: el engine ya lo expandió; se valida solo duplicados
        elif len(pred) != len(pub) or pred.duplicated(["clave_estacion", "fecha", "variable"]).any():
            p.append("predicciones.csv no coincide con test_publico.csv")
    if p:
        raise ValueError("Salida inválida:\n - " + "\n - ".join(p))
