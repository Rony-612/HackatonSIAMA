import React, { useEffect, useMemo, useState } from 'react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ComposedChart, Line, ReferenceLine, Cell, Legend } from 'recharts'
import { get } from './api.js'
import { VARS, MESES, fmt, pct, fdate } from './ui.js'

function useFetch(path) {
  const [s, set] = useState({ d: null, e: null })
  useEffect(() => {
    let live = true
    set({ d: null, e: null })
    get(path).then((d) => live && set({ d, e: null })).catch((e) => live && set({ d: null, e: e.message }))
    return () => { live = false }
  }, [path])
  return s
}
const Loading = () => <div className="empty"><span className="spin" />Cargando…</div>
const axis = { tick: { fontSize: 11, fill: 'var(--muted)' }, axisLine: { stroke: 'var(--line)' }, tickLine: false }

export function DiagPanel({ station, variable }) {
  const { d, e } = useFetch(`/api/v1/estaciones/${station}/diagnostico?variable=${variable}&incluir_huecos=true`)
  const V = VARS[variable]
  if (e) return <div className="err">{e}</div>
  if (!d) return <Loading />
  const years = Object.entries(d.faltantes_por_anio).map(([y, n]) => ({ y, n }))
  const months = Object.entries(d.faltantes_por_mes).map(([m, n]) => ({ m: MESES[m - 1], n }))
  const top = [...(d.huecos || [])].sort((a, b) => b.longitud - a.longitud).slice(0, 8)
  const all = d.huecos || []
  const t0 = all.length ? new Date(all[0].inicio).getTime() : 0
  return (
    <div>
      <div className="kpis">
        <div className="kpi"><small>Vacíos (post-QC)</small><strong>{fmt(d.porcentaje_vacios, 1)} %</strong><span>{d.faltantes} de {d.esperados} días</span></div>
        <div className="kpi"><small>Vacíos en el archivo original</small><strong>{fmt(d.porcentaje_vacios_crudo, 1)} %</strong><span>antes del control de calidad</span></div>
        <div className="kpi"><small>Huecos cortos / medios / largos</small><strong>{d.cortos} · {d.medios} · {d.largos}</strong><span>1–3 d · 4–30 d · &gt;30 d</span></div>
        <div className="kpi"><small>Hueco más largo</small><strong>{d.hueco_mas_largo} d</strong><span>{d.invalidos_o_descartados_qc} datos descartados por QC · {d.sospechosos} sospechosos</span></div>
      </div>

      <div className="two">
        <div>
          <div className="sect">Días faltantes por año</div>
          <div style={{ height: 190 }}>
            <ResponsiveContainer><BarChart data={years} margin={{ left: 0, right: 8, top: 4 }}>
              <CartesianGrid stroke="var(--line)" vertical={false} /><XAxis dataKey="y" {...axis} /><YAxis width={34} {...axis} />
              <Tooltip /><Bar dataKey="n" name="Días faltantes" fill={V.color} radius={[4, 4, 0, 0]} />
            </BarChart></ResponsiveContainer>
          </div>
        </div>
        <div>
          <div className="sect">Días faltantes por mes del año</div>
          <div style={{ height: 190 }}>
            <ResponsiveContainer><BarChart data={months} margin={{ left: 0, right: 8, top: 4 }}>
              <CartesianGrid stroke="var(--line)" vertical={false} /><XAxis dataKey="m" {...axis} /><YAxis width={34} {...axis} />
              <Tooltip /><Bar dataKey="n" name="Días faltantes" fill="var(--accent)" radius={[4, 4, 0, 0]} />
            </BarChart></ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="sect">Dónde caen los huecos en el tiempo</div>
      <Timeline gaps={all} />

      <div className="sect">Los 8 huecos más largos</div>
      <table className="t">
        <thead><tr><th>Inicio</th><th>Fin</th><th>Días</th><th>Tipo</th></tr></thead>
        <tbody>{top.map((g) => (
          <tr key={g.inicio}><td>{fdate(g.inicio)}</td><td>{fdate(g.fin)}</td><td>{g.longitud}</td><td><span className={`tag ${g.tipo}`}>{g.tipo}</span></td></tr>
        ))}</tbody>
      </table>
    </div>
  )
}

function Timeline({ gaps }) {
  if (!gaps.length) return <p className="hint">Sin huecos.</p>
  const t0 = new Date(gaps[0].inicio).getTime()
  const t1 = new Date(gaps.at(-1).fin).getTime()
  const span = Math.max(t1 - t0, 1)
  const col = { corto: '#5aa0d8', medio: '#e0a21b', largo: '#c2410c' }
  return (
    <svg viewBox="0 0 600 34" width="100%" height="44" role="img" aria-label="Línea de tiempo de huecos">
      <rect x="0" y="12" width="600" height="14" rx="4" fill="var(--chip)" />
      {gaps.map((g) => {
        const x = ((new Date(g.inicio).getTime() - t0) / span) * 600
        const w = Math.max(2, ((g.longitud * 86400000) / span) * 600)
        return <rect key={g.inicio} x={x} y="8" width={w} height="22" rx="1.5" fill={col[g.tipo]}><title>{`${fdate(g.inicio)} · ${g.longitud} d`}</title></rect>
      })}
      <text x="0" y="6" fontSize="9" fill="var(--muted)">{fdate(gaps[0].inicio)}</text>
      <text x="600" y="6" fontSize="9" fill="var(--muted)" textAnchor="end">{fdate(gaps.at(-1).fin)}</text>
    </svg>
  )
}

export function ValidationPanel({ station, variable }) {
  const g = useFetch(`/api/v1/metricas?variable=${variable}`)
  const s = useFetch(`/api/v1/metricas?estacion=${station}&variable=${variable}`)
  const rec = useFetch(`/api/v1/recomendacion?variable=${variable}&longitud_hueco=2`)
  if (g.e) return <div className="err">{g.e}</div>
  if (!g.d) return <Loading />
  const v = g.d.variables[variable]
  const c = v.comparacion_comun
  const m = v.modelo
  const rain = variable === 'precipitacion'
  const unit = VARS[variable].unit
  const cov = m.cobertura
  const covOk = Math.abs(cov - 0.9) <= 0.05
  const buckets = ['1-3', '4-30', '31-60', '61+'].filter((b) => v.por_rango_hueco[b]).map((b) => ({
    b: `${b} d`, mae: v.por_rango_hueco[b].mae, cob: v.por_rango_hueco[b].cobertura, n: v.por_rango_hueco[b].n,
  }))
  const flags = v.banderas_validacion
  const st = s.d?.variables?.[variable]
  const warns = (g.d.advertencias || []).filter((w) => w.startsWith(variable))
  const row = (name, x) => (
    <tr key={name}><td><b>{name}</b></td><td>{fmt(x.mae)}</td><td>{fmt(x.rmse)}</td><td>{x.sesgo >= 0 ? '+' : ''}{fmt(x.sesgo)}</td><td>{fmt(x.kge)}</td>{rain && <td>{fmt(x.pod)} / {fmt(x.far)}</td>}</tr>
  )
  return (
    <div>
      {warns.map((w) => <div key={w} className="alert">⚠ {w}</div>)}
      <div className="kpis">
        <div className="kpi"><small>MAE</small><strong>{fmt(m.mae)}</strong><span>{unit} · IDW: {fmt(c.idw.mae)}</span></div>
        <div className="kpi"><small>RMSE</small><strong>{fmt(m.rmse)}</strong><span>{unit}</span></div>
        <div className="kpi"><small>Sesgo (BIAS)</small><strong>{m.sesgo >= 0 ? '+' : ''}{fmt(m.sesgo)}</strong><span>{unit} · estimado − real</span></div>
        <div className="kpi"><small>KGE</small><strong>{fmt(m.kge)}</strong><span>1 = perfecto</span></div>
        {rain && <div className="kpi"><small>POD / FAR</small><strong>{fmt(m.pod)} / {fmt(m.far)}</strong><span>acierto / falsas alarmas</span></div>}
        <div className={`kpi ${covOk ? 'good' : 'bad'}`}><small>Cobertura del intervalo</small><strong>{pct(cov)}</strong><span>nominal 90 %</span></div>
      </div>

      <div className="sect">Modelo contra líneas base (mismas filas, huecos artificiales)</div>
      <table className="t">
        <thead><tr><th>Método</th><th>MAE</th><th>RMSE</th><th>Sesgo</th><th>KGE</th>{rain && <th>POD / FAR</th>}</tr></thead>
        <tbody>{row('GeoTrust (producción)', c.modelo)}{row('IDW', c.idw)}{row(rain ? 'Razón normal (vecinas)' : 'Anomalía de vecinas', c.vecinos)}</tbody>
      </table>
      <p className="hint">n = {c.n} días evaluados. En temperatura la línea base de vecinas ya es fuerte: la mejora del modelo es pequeña y se reporta tal cual.</p>

      <div className="sect">Error y cobertura según el tamaño del hueco</div>
      <div style={{ height: 210 }}>
        <ResponsiveContainer>
          <ComposedChart data={buckets} margin={{ left: 0, right: 8, top: 6 }}>
            <CartesianGrid stroke="var(--line)" vertical={false} />
            <XAxis dataKey="b" {...axis} />
            <YAxis yAxisId="l" width={36} {...axis} />
            <YAxis yAxisId="r" orientation="right" width={40} domain={[0.6, 1]} tickFormatter={(x) => `${Math.round(x * 100)}%`} {...axis} />
            <Tooltip formatter={(val, n) => (n === 'Cobertura' ? pct(val) : fmt(val))} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <ReferenceLine yAxisId="r" y={0.9} stroke="var(--c-alta)" strokeDasharray="4 3" />
            <Bar yAxisId="l" dataKey="mae" name={`MAE (${unit})`} fill={VARS[variable].color} radius={[4, 4, 0, 0]} maxBarSize={44} />
            <Line yAxisId="r" dataKey="cob" name="Cobertura" stroke="var(--accent)" strokeWidth={2} dot={{ r: 3 }} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="two">
        <div>
          <div className="sect">¿Discriminan las banderas?</div>
          <table className="t">
            <thead><tr><th>Bandera</th><th>n</th><th>MAE</th><th>Cobertura</th></tr></thead>
            <tbody>{['estimado_alta', 'estimado_baja'].map((k) => (
              <tr key={k}><td><span className={`tag ${k === 'estimado_alta' ? 'alta' : 'baja'}`}>{k === 'estimado_alta' ? 'alta' : 'baja'}</span></td><td>{flags[k].n}</td><td>{fmt(flags[k].mae)}</td><td>{pct(flags[k].cobertura)}</td></tr>
            ))}</tbody>
          </table>
        </div>
        <div>
          <div className="sect">Método elegido por tamaño de hueco</div>
          <table className="t">
            <thead><tr><th>Hueco</th><th>Método</th></tr></thead>
            <tbody>{Object.entries(v.metodos_por_hueco).map(([k, mt]) => (<tr key={k}><td>{k}</td><td><b>{mt}</b></td></tr>))}</tbody>
          </table>
        </div>
      </div>

      <div className="sect">Recomendación automática de método (MAE por rango de hueco)</div>
      <RecTable rec={v.recomendacion_por_hueco} unit={unit} />

      {st && (
        <>
          <div className="sect">Esta estación ({station})</div>
          <div className="note">MAE {fmt(st.mae)} {unit} · sesgo {st.sesgo >= 0 ? '+' : ''}{fmt(st.sesgo)} · KGE {fmt(st.kge)} · cobertura {pct(st.cobertura)} · n = {st.n}</div>
        </>
      )}
      <p className="hint" style={{ marginTop: 10 }}>{g.d.protocolo.bloques}. {g.d.protocolo.nota}</p>
    </div>
  )
}

function RecTable({ rec, unit }) {
  const names = [...new Set(Object.values(rec).flatMap((r) => Object.keys(r.mae)))]
  return (
    <table className="t">
      <thead><tr><th>Hueco</th>{names.map((n) => <th key={n}>{n}</th>)}<th>Mejor</th></tr></thead>
      <tbody>{Object.entries(rec).map(([b, r]) => (
        <tr key={b}><td>{b} d</td>{names.map((n) => <td key={n} style={{ fontWeight: n === r.mejor ? 700 : 400, color: n === r.mejor ? 'var(--c-alta)' : undefined }}>{r.mae[n] === undefined ? '—' : fmt(r.mae[n])}</td>)}<td><b>{r.mejor}</b></td></tr>
      ))}</tbody>
    </table>
  )
}

export function HomogPanel({ station, variable }) {
  const { d, e } = useFetch('/api/v1/homogeneidad')
  if (e) return <div className="err">{e}</div>
  if (!d) return <Loading />
  const mine = d.filter((r) => r.clave_estacion === station)
  const flagged = d.filter((r) => r.posible_cambio)
  return (
    <div>
      <div className="note">Prueba SNHT sobre la diferencia mensual entre la estación y sus vecinas (temperatura en °C; lluvia como cambio relativo de totales mensuales). Si una serie «salta», rellenarla con su propio historial la empeora: por eso sus estimados nunca llevan alta confianza. Es una heurística, no un dictamen.</div>
      <div className="sect">Esta estación ({station})</div>
      <table className="t">
        <thead><tr><th>Variable</th><th>Estadístico T</th><th>Resultado</th><th>Fecha probable</th><th>Salto</th></tr></thead>
        <tbody>{mine.length ? mine.map((r) => (
          <tr key={r.variable} className={r.variable === variable ? 'hl' : ''}>
            <td>{VARS[r.variable].label}</td><td>{fmt(r.estadistico_T, 1)} (crít. ≈{r.critico_aprox})</td>
            <td>{r.posible_cambio ? <span className="tag ne">posible cambio</span> : <span className="tag alta">sin cambio</span>}</td>
            <td>{r.fecha_probable || '—'}</td><td>{r.posible_cambio ? `${r.salto_medio > 0 ? '+' : ''}${fmt(r.salto_medio, 1)} ${r.unidad_salto}` : '—'}</td>
          </tr>
        )) : <tr><td colSpan="5">Sin análisis (muy pocas vecinas o meses).</td></tr>}</tbody>
      </table>
      <div className="sect">Todas las estaciones con posible cambio abrupto ({flagged.length})</div>
      {flagged.length ? (
        <table className="t">
          <thead><tr><th>Estación</th><th>Variable</th><th>Fecha</th><th>Salto</th></tr></thead>
          <tbody>{flagged.map((r) => (
            <tr key={r.clave_estacion + r.variable}><td><b>{r.clave_estacion}</b></td><td>{VARS[r.variable].label}</td><td>{r.fecha_probable}</td><td>{r.salto_medio > 0 ? '+' : ''}{fmt(r.salto_medio, 1)} {r.unidad_salto}</td></tr>
          ))}</tbody>
        </table>
      ) : <p className="hint">Ninguna.</p>}
    </div>
  )
}
