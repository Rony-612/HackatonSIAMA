# Pitch de 5 minutos — GeoTrust SIAMA

**Mensaje central:** *no solo rellenamos huecos: cada dato estimado dice qué tan seguro es y por qué.*

| Min | Qué se dice | Qué se muestra (demo en vivo) |
|---|---|---|
| 0:00–0:30 | Una serie con huecos es una decisión mal informada: ¿cuánto llovió realmente en la cuenca? | Mapa de Guanajuato coloreado por % de vacíos; estación con aviso de cambio abrupto |
| 0:30–1:15 | Primero diagnosticamos: huecos cortos/medios/largos, cuándo caen y cuántos datos descartó el control de calidad | Pestaña *Diagnóstico*: años, meses, línea de tiempo de huecos |
| 1:15–2:15 | Cómo estimamos: vecinas por correlación (no solo distancia), anomalías en temperatura, lluvia en dos etapas, y un método por tamaño de hueco | Pestaña *Validación*: tabla modelo vs IDW vs vecinas; recomendación por rango de hueco |
| 2:15–3:15 | Incertidumbre honesta: intervalo al 90 % calibrado con conformal; la bandera de calidad se *verifica* con el error real | Serie con banda y tira de calidad; clic en un día → **pasaporte** con vecinas, distancia y correlación |
| 3:15–4:00 | Confianza operativa: cambios abruptos de sensor degradan la bandera; asistente que responde solo con datos y cita su fuente | *Cambios abruptos*; asistente: «¿Cuándo NO debo usar los datos rellenados de 11136?» |
| 4:00–4:40 | Integración: API tipada con OpenAPI, componente React para Monitor Precipitaciones / Temperaturas, Docker | Swagger `/docs`; `?mode=precip&embed=1` |
| 4:40–5:00 | Lo que no prometemos: métricas con datos ficticios; calibración con modelo del 40 %; sesgo positivo en lluvia | Diapositiva de limitaciones |

## Checklist de la demo
1. `uvicorn app.main:app` ya corriendo, navegador en `http://localhost:8000/?station=11096`.
2. Probar sin internet: el mapa base se apaga con el interruptor «Mapa base» y el contorno sigue visible.
3. Tener abierta la ficha técnica y `/docs` en otra pestaña.
4. Plan B: capturas de cada pestaña.

## Preguntas probables
- *¿Por qué no solo IDW?* En temperatura el modelo mejora ~15 % al MAE de IDW; frente a la anomalía de vecinas la mejora es mínima y lo decimos.
- *¿Cómo sé que la bandera sirve?* Se mide: en lluvia las «alta» tienen ~3× menos error; en temperatura la separación es pequeña.
- *¿Qué pasa con huecos de años?* Más de 120 días → `no_estimable` con motivo.
