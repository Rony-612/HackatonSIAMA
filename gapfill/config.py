"""Parámetros del pipeline.

Todo umbral que sea decisión nuestra (no del comité) vive aquí, con su justificación,
para poder cambiarlo y para citarlo en la ficha técnica.
"""
from dataclasses import dataclass, field

VARS = ("precipitacion", "tmax", "tmin")
TEMPS = ("tmax", "tmin")
RAIN_MM = 0.1                 # un día con >= 0.1 mm cuenta como "día con lluvia"
RAIN_MONTHS = (6, 7, 8, 9, 10)  # temporada de lluvias; el resto es estiaje


@dataclass(frozen=True)
class Config:
    # --- vecinos ---
    radius_km: float = 200.0            # radio máximo (SCDNA usa 200 km)
    # Umbrales mirando la distribución real de correlaciones (anomalías: mediana ~0.44 en los datos de ejemplo).
    min_corr: dict = field(default_factory=lambda: {"precipitacion": 0.25, "tmax": 0.30, "tmin": 0.30})
    max_neighbors: int = 20             # SCDNA usa hasta 30; con 16 estaciones nunca se alcanza, con una red grande sí
    min_overlap: int = 45               # días en común mínimos para confiar en una correlación
    min_neighbors_estimate: int = 2     # vecinos observados mínimos para estimar un hueco > 3 días
    min_neighbors_alta: int = 3         # vecinas mínimas para poder declarar 'estimado_alta' (se ajusta si la red es pequeña)
    # --- cuándo NO rellenar ---
    max_fill_days: int = 730            # huecos más largos que esto se declaran no_estimable (validados hasta 365 d)
    # --- QC ---
    temp_range: tuple = (-15.0, 48.0)   # rango físico razonable para Guanajuato (°C)
    rain_max: float = 400.0             # mm/día: por encima es error de captura (récords del estado < 300 mm)
    missing_codes: tuple = (-9999.0, -999.9, -999.0, -99.9, 9999.0, 99999.0, 999.9)  # códigos de "sin dato" comunes en bases mexicanas
    flatline_days: int = 7              # N días consecutivos idénticos (lluvia > 0 o temperaturas) = sensor pegado
    dtr_max: float = 35.0               # Tmax - Tmin mayor que esto es sospechoso (°C)
    robust_z: float = 7.0               # |valor - mediana mensual| / (1.4826·MAD) mayor que esto es atípico...
    robust_abs: float = 6.0             # ...y además se aparta más de esto (°C) de la mediana mensual de la estación
    min_dtr: float = 0.5                # rango diurno mínimo en estimados: Tmax debe ser MAYOR que Tmin (°C)
    same_basin_penalty: float = 0.85    # una vecina de otra cuenca pesa 15 % menos al ordenar candidatas (covariables.gpkg)
    zero_run_days: int = 30             # racha mínima de ceros en lluvias para compararla con las vecinas
    zero_wet_frac: float = 0.25         # ...y es sospechosa si, en promedio, las vecinas tuvieron lluvia en >= esta fracción de esos días
    spike_c: float = 12.0               # salto contra la mediana móvil de 31 días = sospechoso
    # --- validación ---
    split: tuple = (0.4, 0.6, 0.8)      # entrenamiento | selección | calibración | evaluación
    train_rounds: int = 3
    train_coverage: float = 0.25
    train_lengths: tuple = (1, 2, 3, 4, 6, 8, 12, 17, 25, 35, 45, 60, 90, 180, 365)
    eval_lengths: tuple = (1, 2, 3, 8, 17, 35, 45)
    stress_lengths: tuple = (60, 90, 180, 365)    # huecos muy largos (los que no caben en el bloque se omiten solos)
    eval_rounds: int = 3
    # --- incertidumbre ---
    alpha: float = 0.10                 # intervalo nominal 90 %
    min_stratum: int = 40               # muestras mínimas para calibrar un estrato propio
    # --- modelos ---
    smear: bool = True                  # corrección de Duan para el sesgo de retransformar log1p
    seed: int = 784
