import React, { useEffect, useMemo, useState } from 'react'
import { ComposedChart, Area, Line, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ErrorBar } from 'recharts'
import { get } from './api.js'
import { VARS, CALIDAD, fmt, fdate } from './ui.js'

const WINDOWS = [['3 meses', 92], ['6 meses', 183], ['1 año', 365], ['Todo', Infinity]]

function Tip({ active, payload, unit }) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload.p
  const est = p.origen === 'estimado'
  return (
    <div className="tip">
      <b>{fdate(p.fecha)}</b>
      <div><span>{CALIDAD[p.calidad]?.label || p.calidad}</span></div>
      {p.valor !== null && <div><span>Valor</span><b>{fmt(p.valor, 1)} {unit}</b></div>}
      {est && <div><span>Intervalo 90 %</span><span>{fmt(p.limite_inferior, 1)} – {fmt(p.limite_superior, 1)}</span></div>}
      {est && <div><span>Método</span><span>{p.metodo}</span></div>}
      {p.origen !== 'observado' && <div><span>Hueco</span><span>{p.longitud_hueco} d</span></div>}
      {p.origen === 'no_estimable' && <div><span>Motivo</span><span>{p.motivo_no_estimable}</span></div>}
      <div style={{ color: 'var(--muted)', marginTop: 4 }}>Clic para ver el pasaporte del dato</div>
    </div>
  )
}

export default function SeriesPanel({ station, variable }) {
  const V = VARS[variable]
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [win, setWin] = useState(null)       // [i0, i1)
  const [wname, setWname] = useState('1 año')
  const [pick, setPick] = useState(null)

  useEffect(() => {
    setData(null); setErr(null); setPick(null)
    get(`/api/v1/estaciones/${station}/series?variable=${variable}&limite=20000`).then((d) => {
      setData(d)
      const n = d.puntos.length
      setWin([Math.max(0, n - 365), n]); setWname('1 año')
    }).catch((e) => setErr(e.message))
  }, [station, variable])

  const rows = useMemo(() => data?.puntos.map((p) => {
    const est = p.origen === 'estimado'
    return {
      fecha: p.fecha, p,
      obs: p.origen === 'observado' ? p.valor : null,
      est: est ? p.valor : null,
      band: est ? [p.limite_inferior, p.limite_superior] : null,
      err: est ? [p.valor - p.limite_inferior, p.limite_superior - p.valor] : null,
    }
  }), [data])

  const longest = useMemo(() => {
    if (!data) return null
    let best = null, s = -1
    data.puntos.forEach((p, i) => {
      const gap = p.origen !== 'observado'
      if (gap && s < 0) s = i
      if ((!gap || i === data.puntos.length - 1) && s >= 0) {
        const e = gap ? i + 1 : i
        if (!best || e - s > best[1] - best[0]) best = [s, e]
        s = -1
      }
    })
    return best
  }, [data])

  if (err) return <div className="err">{err}</div>
  if (!data || !win) return <div className="empty"><span className="spin" />Cargando serie…</div>

  const slice = rows.slice(win[0], win[1])
  const total = data.total
  const setWindow = (name, n) => {
    setWname(name)
    setWin(n === Infinity ? [0, rows.length] : [Math.max(0, rows.length - n), rows.length])
  }
  const goLongest = () => {
    if (!longest) return
    const mid = Math.floor((longest[0] + longest[1]) / 2)
    const half = Math.max(60, Math.ceil((longest[1] - longest[0]) / 2) + 30)
    setWname('hueco'); setWin([Math.max(0, mid - half), Math.min(rows.length, mid + half)])
  }
  const detail = slice.length <= 300
  const pickedRow = pick && rows.find((r) => r.fecha === pick)

  const common = { data: slice, margin: { top: 8, right: 12, left: 0, bottom: 0 }, onClick: (e) => e?.activeLabel && setPick(e.activeLabel) }
  const axes = (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" vertical={false} />
      <XAxis dataKey="fecha" tick={{ fontSize: 11, fill: 'var(--muted)' }} minTickGap={42} tickFormatter={(s) => fdate(s).replace(/ \d{4}$/, (m) => ` ’${m.slice(3)}`)} axisLine={{ stroke: 'var(--line)' }} tickLine={false} />
      <YAxis width={48} tick={{ fontSize: 11, fill: 'var(--muted)' }} axisLine={false} tickLine={false} domain={variable === 'precipitacion' ? [0, 'auto'] : ['auto', 'auto']} tickFormatter={(v) => Number(v).toFixed(0)} unit="" />
      <Tooltip content={<Tip unit={V.unit} />} cursor={{ stroke: 'var(--accent)', strokeDasharray: '3 3' }} />
    </>
  )

  const cnt = data.conteos
  const order = ['observado', 'estimado_alta', 'estimado_baja', 'no_estimable']

  return (
    <div>
      <div className="row">
        {WINDOWS.map(([n, d]) => <button key={n} className="btn" aria-pressed={wname === n} onClick={() => setWindow(n, d)}>{n}</button>)}
        {longest && <button className="btn" aria-pressed={wname === 'hueco'} onClick={goLongest}>Ir al hueco más largo ({longest[1] - longest[0]} d)</button>}
        <span style={{ marginLeft: 'auto', color: 'var(--muted)', fontSize: 12 }}>{fdate(slice[0]?.fecha)} – {fdate(slice.at(-1)?.fecha)}</span>
      </div>

      <div className="chartbox">
        <ResponsiveContainer width="100%" height="100%">
          {V.kind === 'bar' ? (
            <ComposedChart {...common}>
              {axes}
              <Bar dataKey="obs" stackId="d" fill={V.color} isAnimationActive={false} maxBarSize={14} />
              <Bar dataKey="est" stackId="d" fill={V.color} fillOpacity={0.35} stroke={V.color} strokeDasharray="3 2" isAnimationActive={false} maxBarSize={14}>
                {detail && <ErrorBar dataKey="err" width={4} strokeWidth={1.6} stroke="var(--c-est)" />}
              </Bar>
            </ComposedChart>
          ) : (
            <ComposedChart {...common}>
              {axes}
              <Area dataKey="band" stroke="none" fill={V.color} fillOpacity={0.18} isAnimationActive={false} connectNulls={false} />
              <Line dataKey="obs" stroke={V.color} strokeWidth={1.8} dot={false} isAnimationActive={false} connectNulls={false} />
              <Line dataKey="est" stroke="var(--c-est)" strokeWidth={2} strokeDasharray="5 3" dot={{ r: detail ? 2.5 : 0, fill: 'var(--c-est)', stroke: 'none' }} activeDot={{ r: 4 }} isAnimationActive={false} connectNulls={false}>
                {detail && <ErrorBar dataKey="err" width={4} strokeWidth={1.4} stroke="var(--c-est)" />}
              </Line>
            </ComposedChart>
          )}
        </ResponsiveContainer>
      </div>

      <div className="strip" aria-hidden="true">
        {slice.map((r) => <i key={r.fecha} style={{ background: CALIDAD[r.p.calidad]?.color }} title={`${r.fecha} · ${r.p.calidad}`} />)}
      </div>
      <div className="striplab">Calidad día por día</div>

      <div className="qleg">
        {order.map((k) => (
          <span key={k}><i style={{ background: CALIDAD[k].color }} />{CALIDAD[k].label} <b style={{ color: 'var(--ink)' }}>{cnt[k] ? `${((cnt[k] / total) * 100).toFixed(0)} %` : '0 %'}</b></span>
        ))}
      </div>
      <p className="hint">
        {V.kind === 'bar' ? 'Barras sólidas = medido; barras claras con rayas = estimado, con su intervalo al 90 %.' : 'Línea continua = medido; línea punteada violeta = estimado; banda y barras de error = intervalo al 90 %.'}
        {' '}Haz clic en un día para ver su pasaporte.
      </p>

      {pickedRow && <Passport station={station} variable={variable} row={pickedRow.p} unit={V.unit} onClose={() => setPick(null)} />}
    </div>
  )
}

function Passport({ station, variable, row, unit, onClose }) {
  const [t, setT] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    setT(null); setErr(null)
    get(`/api/v1/estaciones/${station}/series/${row.fecha}/trazabilidad?variable=${variable}`).then(setT).catch((e) => setErr(e.message))
  }, [station, variable, row.fecha])

  const tagCls = row.calidad === 'estimado_alta' ? 'alta' : row.calidad === 'estimado_baja' ? 'baja' : row.calidad === 'no_estimable' ? 'ne' : 'obs'
  const why = {
    observado: 'Medido por la estación y aprobado por el control de calidad. No fue modificado.',
    estimado_alta: 'Varias vecinas bien correlacionadas tenían dato ese día, el hueco es corto y el intervalo es estrecho para su régimen.',
    estimado_baja: 'Hay poco respaldo observacional, el hueco es largo, el intervalo es ancho o la estación tiene un posible cambio abrupto. Úsese con cautela.',
    no_estimable: `No se rellenó: ${row.motivo_no_estimable || 'información insuficiente'}.`,
  }[row.calidad]
  return (
    <div className="passport">
      <div className="row" style={{ marginBottom: 4 }}>
        <h3>Pasaporte del dato · {fdate(row.fecha)}</h3>
        <span className={`tag ${tagCls}`}>{CALIDAD[row.calidad]?.label}</span>
        <button className="btn" style={{ marginLeft: 'auto' }} onClick={onClose}>Cerrar</button>
      </div>
      {row.valor !== null && (
        <div className="big">{fmt(row.valor, 1)} {unit}
          {row.origen === 'estimado' && <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--muted)' }}> &nbsp;intervalo 90 %: {fmt(row.limite_inferior, 1)} – {fmt(row.limite_superior, 1)} {unit}</span>}
        </div>
      )}
      <p style={{ margin: '6px 0 10px' }}>{why}</p>
      {row.origen !== 'observado' && <p style={{ margin: '0 0 8px', color: 'var(--muted)' }}>Método: <b>{row.metodo || '—'}</b> · hueco de <b>{row.longitud_hueco}</b> días · vecinas disponibles: <b>{row.vecinos_disponibles}</b></p>}
      {err && <div className="err">{err}</div>}
      {t && (t.vecinas_con_dato_ese_dia.length ? (
        <table className="t">
          <thead><tr><th>Vecina con dato ese día</th><th>Distancia</th><th>Correlación</th><th>Δ altitud</th><th>Misma cuenca</th><th>Valor</th></tr></thead>
          <tbody>
            {t.vecinas_con_dato_ese_dia.map((v) => (
              <tr key={v.clave_estacion}>
                <td><b>{v.clave_estacion}</b></td><td>{fmt(v.distancia_km, 0)} km</td><td>{fmt(v.correlacion, 2)}</td>
                <td>{v.diferencia_altitud_m > 0 ? '+' : ''}{fmt(v.diferencia_altitud_m, 0)} m</td>
                <td>{v.misma_cuenca === null || v.misma_cuenca === undefined ? '—' : v.misma_cuenca ? 'sí' : 'no'}</td>
                <td>{fmt(v.valor_observado, 1)} {unit}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="hint">Ninguna vecina seleccionada tenía dato observado ese día.</p>)}
      {t && <p className="hint">{t.nota}</p>}
    </div>
  )
}
