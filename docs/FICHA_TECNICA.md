# Ficha técnica — GeoTrust SIAMA

**Reto:** Datos climatológicos sin huecos · Hackatón SIAMA

## 1. Idea en una frase
Cada día faltante se estima con las vecinas correlacionadas, se acompaña de un intervalo calibrado al 90 % y de su *pasaporte* (qué vecinas lo respaldan), y se marca con una bandera de calidad verificada contra el error real.

## 2. Método
- **Modelo:** HistGradientBoosting + cuantiles 5/95 + enrutador por longitud de hueco. Temperatura: el modelo aprende la anomalía respecto a la normal de la estación. Lluvia en dos etapas: ocurrencia (clasificador, umbral elegido por CSI en validación) y cantidad (regresión sobre log1p con corrección de Duan).
- **Vecinas (selección documentada):** radio 200 km, correlación mínima calculada solo con el bloque de entrenamiento (anomalías en temperatura, Spearman en lluvia; mínimos 0.25/0.30/0.30), hasta 20 vecinas ordenadas por correlación, distancia, diferencia de altitud y pertenencia a la misma cuenca (covariables.gpkg).
- **Variables:** vecinas el mismo día (IDW, ponderado por correlación, anomalía o razón normal, máximo, suma de correlaciones), estacionalidad, altitud y coordenadas, normal de la estación, valor anterior/posterior al hueco, días hasta cada uno, interpolación lineal y longitud del hueco.
- **Por tamaño de hueco:** un enrutador elige, con validación, entre `ml`, `vecinos`, `lineal` (≤30 d) y `ens` (mezcla ml+vecinos). Elegido: Precipitación: corto=ens/medio=vecinos/largo=ens; Tmax: corto=lineal/medio=ml/largo=ens_local; Tmin: corto=lineal/medio=ens_local/largo=ens_local.
- **Incertidumbre:** Conformal CQR de Mondrian (tipo de hueco x temporada x régimen húmedo/seco). En lluvia el error se mide en escala log1p y por régimen predicho (seco/húmedo), para que el ajuste no se anule con los días secos.
- **Control de calidad previo:** códigos de faltante, rangos físicos, lluvia > 400 mm, Tmax<Tmin, rango diurno > 35 °C, sensor pegado (≥7 días iguales), ceros de lluvia solo si las vecinas sí llovieron (nunca por la racha sola), saltos extremos. Lo severo se descarta antes de entrenar y de usarse como vecina.
- **Coherencia física:** precipitación ≥ 0 y Tmax > Tmin (rango mínimo 0.5 °C) en toda salida; los intervalos se recortan contra el dato observado complementario.

## 3. Validación
40% entrenamiento / 20% selección / 20% calibración / 20% evaluación (cronológicos); huecos artificiales de 1, 2, 3, 8, 17, 35, 45, 60, 90, 180, 365 días; reentrenado final con todo el histórico. Datos: CLICOM-CONAGUA oficiales (11096 Salamanca, 11136 La Joyita); particiones desde el inicio de operación conjunta (1977-10-01). Las métricas son sobre huecos artificiales enmascarados en los datos reales y **no son** la puntuación del conjunto oculto.

| Variable | MAE modelo | MAE IDW | MAE vecinas | RMSE | Sesgo | KGE | POD / FAR | Cobertura (90 %) |
|---|---|---|---|---|---|---|---|---|
| Precipitación (mm) | 1.82 | 1.78 | 1.83 | 5.39 | -0.03 | 0.58 | 0.81 / 0.38 | 96 % |
| Tmax (°C) | 1.21 | 1.52 | 1.48 | 1.55 | +0.04 | 0.82 | — | 94 % |
| Tmin (°C) | 1.25 | 2.52 | 2.66 | 1.58 | -0.06 | 0.87 | — | 86 % |

**MAE / cobertura por tamaño de hueco**

| Variable | 1-3 d | 4-30 d | 31-60 d | 61+ d |
|---|---|---|---|---|
| Precipitación (mm) | 3.15 / 89 % | 1.47 / 97 % | 2.06 / 96 % | 1.70 / 97 % |
| Tmax (°C) | 1.20 / 89 % | 1.46 / 95 % | 1.27 / 100 % | 1.15 / 93 % |
| Tmin (°C) | 1.11 / 95 % | 1.09 / 98 % | 0.95 / 98 % | 1.34 / 82 % |

**¿Discriminan las banderas?** (MAE y cobertura, alta vs baja)

| Variable | alta (n, MAE, cob.) | baja (n, MAE, cob.) |
|---|---|---|
| Precipitación (mm) | 181, 1.14, 96 % | 1189, 1.97, 96 % |
| Tmax (°C) | 132, 1.25, 89 % | 1076, 1.20, 95 % |
| Tmin (°C) | 109, 0.99, 97 % | 1445, 1.27, 85 % |

## 4. Banderas de calidad
`observado` · `estimado_alta` (≥3 vecinas observadas, suma de correlaciones sobre la mediana de su régimen, hueco ≤30 d, intervalo ≤ percentil 75 de su régimen y sin cambio abrupto en la estación) · `estimado_baja` · `no_estimable` (hueco >120 d o <2 vecinas con dato en huecos >3 d, con motivo explícito).

## 5. Extras del reto
- **Recomendación de método por longitud de hueco:** `/api/v1/recomendacion` y tabla en la pestaña Validación (MAE de ml, vecinos, lineal, ens e IDW por rango).
- **Cambios abruptos (SNHT):** temperatura (°C) y precipitación (cambio relativo de totales mensuales); alimenta la bandera de calidad. Detectados en estos datos: 11096:tmax, 11096:tmin, 11136:tmax, 11136:tmin.
- **Asistente:** `POST /api/v1/asistente`, responde con reglas deterministas ancladas a los resultados (cita el endpoint) y, opcionalmente, IBM Granite (watsonx.ai u Ollama) solo reescribe la respuesta; si una cifra no está en el contexto se descarta el texto generado.
- **Contenedor:** Dockerfile (usuario no root, HEALTHCHECK), `.dockerignore` y `docker-compose.yml`.

## 6. Integración con SIAMA
API REST de solo lectura con esquemas Pydantic y OpenAPI exportado (`docs/openapi.json`). Front en React + Leaflet + Recharts, consumible como componente `<SiamaGapfill mode="precip"|"temp" apiBase=… />` o en iframe (`?embed=1`).

## 7. Limitaciones conocidas (sin adornos)
- Los intervalos se calibran con el modelo entrenado en el 40 % inicial y se aplican al modelo reentrenado con todo el histórico; no es CV+ conformal. Con tendencia climática fuerte puede descalibrarse.
- En temperatura, la línea base «anomalía de vecinas» es casi tan buena como el modelo; la mejora del ML es de centésimas de °C.
- Las banderas separan bien el error en lluvia, pero en temperatura la diferencia alta/baja es pequeña.
- **Red de solo 2 estaciones (~6 km):** cada estación tiene como máximo 1 vecina; cuando faltan las dos el día es `no_estimable`. En lluvia el ML no mejora de forma medible al IDW (MAE similar; POD algo mayor).
- La diferencia Tmin entre estaciones deriva de −2 a +3 °C en 40 años: se usa el desfase local (±90 d) y el enrutador elige por tipo de hueco; la cobertura de Tmin queda cerca de 86 %, bajo el 90 % nominal.
- La bandera `alta` en Tmax no discrimina error; en lluvia y Tmin sí.
- La cobertura del intervalo de lluvia es mayor que 90 % (intervalos conservadores).
- No se usan covariables externas ni reanálisis (NASA POWER / ERA5-Land): no eran descargables en el entorno de desarrollo.
- No se probó con Parquet ni GeoJSON reales, ni con PostgreSQL/PostGIS; el contenedor no se construyó en el entorno de desarrollo.
- Advertencia del propio pipeline: precipitacion: cobertura global 0.96 se aleja del nominal 0.90.
- Advertencia del propio pipeline: tmax: la bandera 'alta confianza' NO tiene menos error que 'baja' (MAE 1.25 vs 1.20).

## 8. Reproducibilidad
`pip install -r requirements.txt` · `python -m gapfill.cli --data-dir data_oficial` · `pytest` · `uvicorn app.main:app` · front: `cd frontend && npm install && npm run build` (el resultado ya viene en `web/`).