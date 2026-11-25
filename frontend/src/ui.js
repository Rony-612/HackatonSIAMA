export const VARS = {
  precipitacion: { label: 'Precipitación', short: 'Lluvia', unit: 'mm', color: '#2563eb', kind: 'bar' },
  tmax: { label: 'Temperatura máxima', short: 'Tmax', unit: '°C', color: '#e0572b', kind: 'line' },
  tmin: { label: 'Temperatura mínima', short: 'Tmin', unit: '°C', color: '#0e9bb5', kind: 'line' },
}
export const MODES = { all: ['precipitacion', 'tmax', 'tmin'], precip: ['precipitacion'], temp: ['tmax', 'tmin'] }

export const CALIDAD = {
  observado: { label: 'Observado', color: 'var(--c-obs)' },
  estimado_alta: { label: 'Estimado · alta confianza', color: 'var(--c-alta)' },
  estimado_baja: { label: 'Estimado · baja confianza', color: 'var(--c-baja)' },
  no_estimable: { label: 'No estimable', color: 'var(--c-ne)' },
}

// escala secuencial para % de vacíos (mapa)
export const GAP_BREAKS = [
  { max: 5, color: '#cfe3f6', label: '< 5 %' },
  { max: 10, color: '#8fbbe6', label: '5–10 %' },
  { max: 15, color: '#4f8fd1', label: '10–15 %' },
  { max: 25, color: '#2a5fa8', label: '15–25 %' },
  { max: Infinity, color: '#13306b', label: '> 25 %' },
]
export const gapColor = (p) => (GAP_BREAKS.find((b) => (p ?? 0) < b.max) || GAP_BREAKS.at(-1)).color

export const MESES = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
export const fmt = (x, d = 2) => (x === null || x === undefined || Number.isNaN(x) ? '—' : Number(x).toFixed(d))
export const pct = (x, d = 0) => (x === null || x === undefined ? '—' : `${(x * 100).toFixed(d)} %`)
export const fdate = (s) => {
  if (!s) return ''
  const [y, m, d] = s.split('-')
  return `${Number(d)} ${MESES[Number(m) - 1]} ${y}`
}

// Escalas del Monitor de Clima de SIGWEB: temperatura azul→verde→amarillo→rojo (5 a 40 °C); lluvia en azules.
const TEMP_STOPS = [[5, '#1d4ed8'], [10, '#0ea5e9'], [15, '#22c55e'], [22, '#84e32d'], [28, '#facc15'], [34, '#f97316'], [40, '#dc2626']]
const RAIN_STOPS = [[0, '#e5eef9'], [1, '#bcd8f5'], [5, '#7fb2ec'], [10, '#3f86d9'], [20, '#2358b5'], [50, '#17306f']]
const lerp = (a, b, t) => a + (b - a) * t
const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16))
function ramp(stops, v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '#9aa5b5'
  if (v <= stops[0][0]) return stops[0][1]
  if (v >= stops.at(-1)[0]) return stops.at(-1)[1]
  const i = stops.findIndex(([x]) => v < x)
  const [x0, c0] = stops[i - 1]
  const [x1, c1] = stops[i]
  const t = (v - x0) / (x1 - x0)
  const a = hex(c0), b = hex(c1)
  return `rgb(${a.map((u, k) => Math.round(lerp(u, b[k], t))).join(',')})`
}
export const valueColor = (variable, v) => ramp(variable === 'precipitacion' ? RAIN_STOPS : TEMP_STOPS, v)
export const valueStops = (variable) => (variable === 'precipitacion' ? RAIN_STOPS : TEMP_STOPS)
export const fmtVal = (variable, v) => (v === null || v === undefined ? '—' : variable === 'precipitacion' ? (v === 0 ? '0' : Number(v).toFixed(1)) : Number(v).toFixed(1))
