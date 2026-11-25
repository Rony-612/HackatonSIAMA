# Diccionario sintético SIAMA
Todos los datos y estaciones son ficticios. No usar para decisiones reales.

- clave_estacion: ID de estación simulada.
- fecha: YYYY-MM-DD, frecuencia diaria.
- precipitacion: mm/día; 0 es ausencia real de lluvia.
- tmax, tmin: °C.
- NaN/celda vacía y -9999: dato faltante. También hay días sin fila.
- latitud/longitud: WGS84, grados; altitud: metros.
- inicio_operacion, fin_operacion: intervalo inclusivo esperado.
- test_publico.csv: claves estación/fecha/variable a estimar, sin respuestas.
- covariables.gpkg: puntos de elevación y polígonos ilustrativos; no son límites oficiales ni un DEM real.
