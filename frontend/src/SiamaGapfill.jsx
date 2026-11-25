import React, { useEffect, useMemo, useState } from 'react'
import { get, setBase, docsUrl, downloadUrl } from './api.js'
import { VARS, MODES, GAP_BREAKS, gapColor, valueColor, valueStops, CALIDAD } from './ui.js'
import MapView from './MapView.jsx'
import SeriesPanel from './SeriesPanel.jsx'
import { DiagPanel, ValidationPanel, HomogPanel } from './Panels.jsx'
import Assistant from './Assistant.jsx'

const TABS = [
  ['serie', 'Serie y confianza'],
  ['diag', 'Diagnóstico de vacíos'],
  ['valid', 'Validación'],
  ['hom', 'Cambios abruptos'],
  ['asis', 'Asistente'],
]

function Logo() {
  return (
    <svg width="34" height="34" viewBox="0 0 32 32" aria-hidden="true">
      <circle cx="16" cy="16" r="15" fill="#1d4ed8" />
      <path d="M8 19c3-6 6-6 8-2s5 4 8-3" stroke="#fff" strokeWidth="2.2" fill="none" strokeLinecap="round" />
      <path d="M9 24l4 3 10-10" stroke="#9fe3c9" strokeWidth="2.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/**
 * Componente integrable en Monitor Precipitaciones (mode="precip") y Monitor Temperaturas (mode="temp").
 * Solo lee la API REST: no calcula nada en el navegador.
 */
export default function SiamaGapfill({ mode = 'all', embed = false, apiBase = '', initialStation = null, initialVariable = null }) {
  setBase(apiBase)
  const vars = MODES[mode] || MODES.all
  const [variable, setVariable] = useState(vars.includes(initialVariable) ? initialVariable : vars[0])
  const [stations, setStations] = useState(null)
  const [selected, setSelected] = useState(initialStation)
  const [tab, setTab] = useState('serie')
  const [error, setError] = useState(null)
  const [info, setInfo] = useState(null)
  const [layer, setLayer] = useState('dia')       // 'dia' = valor del día (como el Monitor de Clima) | 'vacios' = completitud
  const [fecha, setFecha] = useState(null)
  const [day, setDay] = useState(null)

  useEffect(() => {
    get('/api/v1/proyecto').then(setInfo).catch(() => {})
  }, [])
  useEffect(() => {
    setStations(null)
    get(`/api/v1/estaciones?variable=${variable}`)
      .then((d) => {
        setStations(d)
        setSelected((s) => s || d.estaciones[0]?.clave_estacion)
      })
      .catch((e) => setError(e.message))
  }, [variable])

  useEffect(() => {
    if (layer !== 'dia') return
    get(`/api/v1/mapa?variable=${variable}${fecha ? `&fecha=${fecha}` : ''}`)
      .then((d) => { setDay(d); if (!fecha) setFecha(d.fecha) })
      .catch((e) => setError(e.message))
  }, [variable, fecha, layer])
  const shift = (n) => { const d = new Date(`${fecha}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + n); setFecha(d.toISOString().slice(0, 10)) }

  const st = useMemo(() => stations?.estaciones.find((e) => e.clave_estacion === selected), [stations, selected])
  const tabs = embed ? TABS.filter(([k]) => k !== 'asis' || mode === 'all') : TABS

  if (error) return <div className="err">No se pudo conectar con la API: {error}. ¿Está corriendo <code>uvicorn app.main:app</code>?</div>

  return (
    <div className={`app ${embed ? 'embed' : ''}`}>
      {!embed && (
        <header className="top">
          <div className="brand">
            <Logo />
            <div>
              <h1>{info?.nombre || 'GeoTrust SIAMA'}</h1>
              <p>Datos climatológicos sin huecos · cada estimado con su pasaporte</p>
            </div>
          </div>
          <VarSwitch vars={vars} value={variable} onChange={setVariable} />
          <nav className="links">
            <a href={docsUrl()} target="_blank" rel="noreferrer">API (Swagger)</a>
            <a href={downloadUrl('predicciones.csv')}>predicciones.csv</a>
          </nav>
        </header>
      )}
      {embed && vars.length > 1 && (
        <div style={{ padding: '8px 8px 0' }}><VarSwitch vars={vars} value={variable} onChange={setVariable} /></div>
      )}

      <main className="layout">
        <section className="left">
          <div className="card mapwrap">
            <div className="mapbar">
              <h2>{layer === 'dia' ? `${VARS[variable].label} · valor del día` : `Estaciones · % de vacíos en ${VARS[variable].label.toLowerCase()}`}</h2>
              <div className="seg sm" role="group" aria-label="Capa">
                <button aria-pressed={layer === 'dia'} onClick={() => setLayer('dia')}>Valor del día</button>
                <button aria-pressed={layer === 'vacios'} onClick={() => setLayer('vacios')}>Completitud</button>
              </div>
            </div>
            {layer === 'dia' && day && (
              <div className="datebar">
                <button onClick={() => shift(-1)} disabled={fecha <= day.fecha_min} aria-label="Día anterior">‹</button>
                <input type="date" value={fecha || ''} min={day.fecha_min} max={day.fecha_max} onChange={(e) => e.target.value && setFecha(e.target.value)} aria-label="Fecha" />
                <button onClick={() => shift(1)} disabled={fecha >= day.fecha_max} aria-label="Día siguiente">›</button>
              </div>
            )}
            <MapView stations={stations?.estaciones} selected={selected} onSelect={setSelected} layer={layer} day={day} variable={variable} />
            {layer === 'dia' ? <DayLegend variable={variable} /> : <Legend />}
          </div>
          {!embed && <StationList list={stations?.estaciones} selected={selected} onSelect={setSelected} />}
        </section>

        <section className="card panel">
          {!st ? (
            <div className="empty"><span className="spin" />Cargando estaciones…</div>
          ) : (
            <>
              <div className="phead">
                <div>
                  <h2>{st.nombre.replace('Estación sintética ', '')} · {st.clave_estacion}</h2>
                  <div className="sub">{st.municipio} · {Math.round(st.altitud)} m s. n. m. · {st.latitud.toFixed(2)}°, {st.longitud.toFixed(2)}°</div>
                </div>
                <div className="chips">
                  <span className="chip">Vacíos <b>{st.porcentaje_vacios?.toFixed(1)} %</b></span>
                  <span className="chip">Hueco máx. <b>{st.hueco_mas_largo} d</b></span>
                  {st.cambio_abrupto && <span className="chip warnc">⚠ posible cambio abrupto</span>}
                </div>
              </div>
              <div className="tabs" role="tablist">
                {tabs.map(([k, l]) => (
                  <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{l}</button>
                ))}
              </div>
              <div className="body">
                {tab === 'serie' && <SeriesPanel key={`${selected}-${variable}`} station={selected} variable={variable} />}
                {tab === 'diag' && <DiagPanel key={`${selected}-${variable}`} station={selected} variable={variable} />}
                {tab === 'valid' && <ValidationPanel key={`${selected}-${variable}`} station={selected} variable={variable} />}
                {tab === 'hom' && <HomogPanel station={selected} variable={variable} />}
                {tab === 'asis' && <Assistant station={selected} variable={variable} />}
              </div>
            </>
          )}
        </section>
      </main>
      {!embed && (
        <footer className="foot">
          Un dato estimado nunca se presenta como observado. Intervalos con cobertura nominal del 90 % (conformal CQR por estrato).
        </footer>
      )}
    </div>
  )
}

function VarSwitch({ vars, value, onChange }) {
  if (vars.length < 2) return null
  return (
    <div className="seg" role="group" aria-label="Variable">
      {vars.map((v) => (
        <button key={v} aria-pressed={v === value} onClick={() => onChange(v)}>
          <span className="dot" style={{ background: VARS[v].color }} />{VARS[v].label}
        </button>
      ))}
    </div>
  )
}

function DayLegend({ variable }) {
  const stops = valueStops(variable)
  return (
    <div className="legend">
      <b>{VARS[variable].label} ({VARS[variable].unit})</b>
      <div className="grad" style={{ background: `linear-gradient(to right, ${stops.map(([, c]) => c).join(',')})` }} />
      <div className="gradlab"><span>{stops[0][0]}</span><span>{stops.at(-1)[0]}{variable === 'precipitacion' ? '+' : ''}</span></div>
      <div><i className="pinkey" />Borde sólido: observado</div>
      <div><i className="pinkey est" />Punteado: estimado ({Object.keys(CALIDAD).length ? 'con intervalo' : ''})</div>
    </div>
  )
}

function Legend() {
  return (
    <div className="legend">
      <b>Datos faltantes</b>
      {GAP_BREAKS.map((b) => (<div key={b.label}><i style={{ background: b.color }} />{b.label}</div>))}
      <div><i className="ring" />Cambio abrupto</div>
    </div>
  )
}

function StationList({ list, selected, onSelect }) {
  if (!list) return null
  const rows = [...list].sort((a, b) => (b.porcentaje_vacios ?? 0) - (a.porcentaje_vacios ?? 0))
  const max = Math.max(...rows.map((r) => r.porcentaje_vacios ?? 0), 1)
  return (
    <div className="card stlist">
      <table>
        <thead><tr><th>Estación</th><th>Municipio</th><th>Vacíos</th><th></th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.clave_estacion} aria-selected={r.clave_estacion === selected} onClick={() => onSelect(r.clave_estacion)}>
              <td>{r.clave_estacion}{r.cambio_abrupto && <span className="warn"> ⚠</span>}</td>
              <td>{r.municipio}</td>
              <td>{r.porcentaje_vacios?.toFixed(1)} %</td>
              <td><div className="bar"><i style={{ width: `${((r.porcentaje_vacios ?? 0) / max) * 100}%`, background: gapColor(r.porcentaje_vacios) }} /></div></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
