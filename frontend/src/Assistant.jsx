import React, { useRef, useState, useEffect } from 'react'
import { post } from './api.js'

const SUG = [
  '¿Qué tan completa está la estación {s}?',
  '¿Cuándo NO debo usar los datos rellenados de {s}?',
  '¿{s} tiene cambios abruptos de sensor?',
  '¿Qué tan confiable es el modelo en esta estación?',
]

export default function Assistant({ station, variable }) {
  const [msgs, setMsgs] = useState([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const end = useRef(null)
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }) }, [msgs])

  const ask = async (text) => {
    const pregunta = text.trim()
    if (pregunta.length < 3) return
    setMsgs((m) => [...m, { r: 'u', t: pregunta }])
    setQ('')
    setBusy(true)
    try {
      const r = await post('/api/v1/asistente', { pregunta, clave_estacion: station, variable })
      setMsgs((m) => [...m, { r: 'a', t: r.respuesta, f: r.fuentes, g: r.generado_por, v: r.verificacion }])
    } catch (e) {
      setMsgs((m) => [...m, { r: 'a', t: `No pude responder: ${e.message}` }])
    }
    setBusy(false)
  }

  return (
    <div>
      <div className="note">Asistente anclado a los datos: responde solo con lo que está en los resultados del pipeline (diagnóstico, series, vecinas, métricas y homogeneidad) y cita el endpoint de donde sale cada respuesta.</div>
      <div className="sugg" style={{ marginTop: 10 }}>
        {SUG.map((s) => { const t = s.replace('{s}', station); return <button key={s} onClick={() => ask(t)}>{t}</button> })}
      </div>
      <div className="chat">
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.r}`}>
            {m.t}
            {m.f?.length > 0 && <small>Fuente: {m.f.join(' · ')}</small>}
            {m.g && <small>Redactado por: {m.g}{m.v && m.v !== 'no aplica' ? ` · ${m.v}` : ''}</small>}
          </div>
        ))}
        {busy && <div className="msg a"><span className="spin" />Consultando datos…</div>}
        <div ref={end} />
      </div>
      <form className="ask" onSubmit={(e) => { e.preventDefault(); ask(q) }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Pregunta sobre ${station}…`} aria-label="Pregunta" />
        <button className="btn primary" type="submit" disabled={busy}>Preguntar</button>
      </form>
    </div>
  )
}
