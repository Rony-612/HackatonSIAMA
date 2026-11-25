# GeoTrust SIAMA — Datos climatológicos sin huecos

*Cada dato estimado con su pasaporte.*

Reconstruye huecos históricos de **precipitación diaria, Tmax y Tmin** y entrega, para cada día: valor, intervalo al 90 %, origen (`observado` / `estimado` / `no_estimable`), bandera de calidad (`observado`, `estimado_alta`, `estimado_baja`, `no_estimable`) y su **pasaporte** (qué vecinas observadas lo respaldan). Un dato observado válido nunca se modifica.

![stack](https://img.shields.io/badge/stack-Python%20·%20FastAPI%20·%20React%20·%20Leaflet%20·%20Recharts-1d4ed8)

## Inicio rápido

Requiere Python 3.11+ (y Node 20+ solo si quieres recompilar el front).

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m gapfill.cli --data-dir data_oficial   # pipeline completo, un solo comando -> outputs/ (~45 s)
uvicorn app.main:app --port 8000 # API + front  ->  http://127.0.0.1:8000   ·   Swagger: /docs
pytest -q                        # 43 pruebas
python scripts/make_ficha.py     # ficha técnica con los números reales -> docs/FICHA_TECNICA.md
python scripts/export_openapi.py # esquema OpenAPI estático -> docs/openapi.json
```

El resultado de `python -m gapfill.cli` ya viene en `outputs/` y el front ya compilado en `web/`.
**Docker:** `docker compose up` (API + front) · `docker compose run --rm --profile tools recalcular`.

## Datos oficiales (CLICOM-CONAGUA)

`data_oficial/` contiene `dia11096.txt` (Salamanca), `dia11136.txt` (La Joyita) y el CSV CLICOM 1981-2025. `gapfill/ingesta.py` los detecta solos, trata `NULO` como faltante, fusiona fuentes (cuenta conflictos) y ajusta los umbrales a una red de 2 estaciones. Las métricas son sobre huecos artificiales en esos datos reales. Con una sola estación el pipeline se detiene con un mensaje claro (sin vecinas no hay con qué estimar). Los resultados de la versión sintética quedan en `outputs_sintetico/`.

## Qué hay dentro

```
gapfill/            pipeline
  data.py           carga (CSV/Parquet/GeoJSON), QC, calendario, periodo de operación (explícito o inferido)
  gaps.py           longitud/tipo de hueco y diagnóstico
  neighbors.py      normales climáticas + vecinas por correlación, distancia, altitud y cuenca
  features.py       variables: vecinas, propia serie, contexto, respaldo observacional
  design.py         diseño para el modelo (anomalías en temperatura)
  modelo.py         GradientBoosting + cuantiles + enrutador de métodos (ml / vecinos / lineal / ens)
  conformal.py      conformal CQR de Mondrian (tipo de hueco × temporada × régimen)
  quality.py        banderas por evidencia
  homogeneity.py    SNHT para temperatura y precipitación
  engine.py         entrenar → afinar → calibrar → evaluar → reentrenar → publicar
  validate.py       contrato de salida: si falla, no se publica nada
app/main.py         API REST (esquemas Pydantic, OpenAPI, asistente)
frontend/           React + Leaflet + Recharts (fuente);  web/ = build servido por la API
tests/              pruebas
docs/               ficha técnica, pitch, openapi.json
data/  outputs/     datos de ejemplo y resultados
```

## API (Swagger en `/docs`)

| Endpoint | Qué devuelve |
|---|---|
| `GET /api/v1/estaciones?variable=` | estaciones, % de vacíos, alerta de cambio abrupto (mapa) |
| `GET /api/v1/estaciones/{id}/diagnostico?variable=&incluir_huecos=` | **(1)** diagnóstico de vacíos (por año, mes, lista de huecos) |
| `GET /api/v1/estaciones/{id}/series?variable=&inicio=&fin=&limite=&offset=` | **(2)** serie con origen, bandera e intervalo por día (paginada) |
| `GET /api/v1/metricas?variable=&estacion=` | **(3)** MAE, RMSE, sesgo, KGE, POD/FAR, cobertura; global o por estación |
| `GET /api/v1/recomendacion?variable=&longitud_hueco=` | método recomendado según el tamaño del hueco |
| `GET /api/v1/estaciones/{id}/series/{fecha}/trazabilidad` | pasaporte del dato |
| `GET /api/v1/homogeneidad?estacion=&variable=&solo_cambios=` | cambios abruptos (SNHT) |
| `POST /api/v1/asistente` | preguntas en lenguaje natural, respuestas ancladas a los resultados, con fuente |
| `GET /api/v1/banderas`, `/proyecto`, `/descargas/{archivo}` | meta y descargas |

Errores: 404 (estación/fecha inexistente), 422 (parámetro inválido), 503 (aún no hay resultados).

## Integración con Monitor Precipitaciones / Temperaturas

```jsx
import SiamaGapfill from './SiamaGapfill'
<SiamaGapfill mode="precip" apiBase="https://api.siama.example" embed />   // o mode="temp"
```
o por iframe: `https://HOST/?mode=temp&embed=1&station=SIM001&variable=tmax&api=https://HOST`.
Dependencias locales (npm), sin CDN de gráficas; el mapa base de CARTO/OSM es opcional (interruptor) y el contorno de Guanajuato se ve sin internet.

## Cómo se evita hacer trampa

- Bloques **cronológicos**: entrenamiento 40 % · selección 20 % · calibración 20 % · evaluación 20 %.
- Vecinas, correlaciones y normales se calculan **solo con el bloque de entrenamiento**; los huecos artificiales se aplican **antes** de construir variables.
- Ninguna verdad de evaluación vive en este repositorio (`.gitignore` y `.dockerignore` la excluyen).
- Al final se reentrena con todo el histórico conservando lo decidido en validación.

## Adaptar a datos oficiales

1. Nombres de columnas y códigos de faltante: `gapfill/data.py` y `config.py` (`missing_codes`). Revisa el diccionario de datos del comité.
2. `test_publico` en formato largo (`clave_estacion, fecha, variable`) o ancho (celdas vacías = objetivos); es opcional.
3. Si `estaciones` no trae `inicio_operacion`/`fin_operacion`, se infieren de la primera/última observación.
4. **Mira la distribución de correlaciones antes de fijar `min_corr`** (`metricas.json → vecinas_por_estacion`).
5. Revisa `metricas.json → advertencias`; `validate.py` aborta si la salida viola el contrato.

## Limitaciones conocidas

Ver `docs/FICHA_TECNICA.md`, sección 7. Las principales: intervalos calibrados con el modelo del 40 % y aplicados al reentrenado; en temperatura el ML mejora poco a la anomalía de vecinas; sesgo positivo en lluvia; `covariables.gpkg` de ejemplo es ilustrativo; Docker y Parquet/GeoJSON sin probar con datos reales.

## Integración con SIGWEB (Monitor de Clima)

El Monitor de Clima de SIGWEB es un mapa **Leaflet** con una lista lateral de capas (Temperatura, Mínima 08:00, Máxima 17:00,
Precipitación, Acumulada…) y pines con el valor por estación. GeoTrust se integra de dos formas, sin tocar su código:

1. **Capa sobre su mapa** (recomendada): una línea sobre el mapa Leaflet que ya existe.
   ```html
   <script src=".../widget/siama-gapfill.js"></script>
   <script>const gt = SiamaGapfill.addToLeafletMap(mapa, { apiBase: 'https://TU-API', variable: 'tmax', onSelect: (clave) => … })</script>
   ```
   Pinta el valor del día con la misma escala azul→rojo, borde **sólido = observado**, **punteado = estimado**, y un popup con el mismo
   formato de SIGWEB (Estación / valor / Municipio / Dependencia) más intervalo y bandera. `gt.setVariable('precipitacion')`, `gt.setDate('AAAA-MM-DD')`, `gt.remove()`.
2. **Panel completo** (serie con intervalo, diagnóstico, validación): `SiamaGapfill.mount(elemento, { apiBase, mode: 'precip' | 'temp', station })`
   con `widget/siama-gapfill.css`.

Prueba local: `python -m uvicorn app.main:app` y abrir `http://localhost:8000/demo-sigweb.html` (maqueta del Monitor; **no es el sitio real**).
Reconstruir el front: `cd frontend && npm install && npm run build:all`. Requisitos para producción: la API con **HTTPS** y CORS restringido al dominio de SIAMA (hoy `*`).
Endpoint nuevo: `GET /api/v1/mapa?variable=&fecha=` (un día, todas las estaciones).

Por confirmar con SIAMA: el Monitor usa acumulado de lluvia de 8:00 a 8:00 y Mínima 08:00 / Máxima 17:00; hay que verificar a qué ventana corresponde el «día» de los datos del reto.
