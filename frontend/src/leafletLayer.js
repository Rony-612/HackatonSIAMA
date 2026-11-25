// Capa para un mapa Leaflet YA EXISTENTE (como el Monitor de Clima de SIGWEB): no crea mapa ni usa React.
//   const gt = SiamaGapfill.addToLeafletMap(mapaDelSitio, { apiBase: 'https://api…', variable: 'tmax', onSelect: (clave) => … })
//   gt.setVariable('precipitacion'); gt.setDate('2024-07-10'); gt.remove()
// Usa el Leaflet del sitio anfitrión (window.L) para no cargar uno segundo.
import { valueColor, fmtVal, VARS, CALIDAD } from './ui.js'

const CSS = `.gt-wrap{background:none;border:0}
.gt-pin{position:relative;width:38px;height:38px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);background:var(--c);border:2.5px solid #fff;box-shadow:0 1px 5px rgba(0,0,0,.45);display:grid;place-items:center}
.gt-pin span{transform:rotate(45deg);font:700 11px/1 system-ui,sans-serif;color:#0b1220;background:rgba(255,255,255,.88);border-radius:9px;padding:2px 4px;min-width:22px;text-align:center}
.gt-pin.est{border:2.5px dashed #5b21b6;box-shadow:0 0 0 2px rgba(255,255,255,.9),0 1px 5px rgba(0,0,0,.45)}
.gt-pin.none{opacity:.7;filter:grayscale(1)}
.gt-title{color:#1d4ed8;font-weight:700;font-size:15px;text-align:center;margin-bottom:6px}
.gt-row{font-size:12.5px;margin:2px 0}.gt-flag{font-weight:700;font-size:12.5px;margin-top:6px}.gt-date{font-size:11px;color:#667085;margin-top:4px}`

const CAL_COLOR = { observado: '#1d4ed8', estimado_alta: '#0f8a5f', estimado_baja: '#b7791f', no_estimable: '#667085' }
const esc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))

export function addToLeafletMap(map, opts = {}) {
  const L = opts.L || window.L
  if (!L) throw new Error('SiamaGapfill.addToLeafletMap: no encontré Leaflet (window.L)')
  const base = (opts.apiBase || '').replace(/\/$/, '')
  let variable = opts.variable || 'tmax'
  let fecha = opts.fecha || null
  const group = L.layerGroup().addTo(map)
  if (!document.getElementById('gt-layer-css')) {
    const st = document.createElement('style'); st.id = 'gt-layer-css'; st.textContent = CSS; document.head.appendChild(st)
  }

  const popup = (p, d) => {
    const u = VARS[variable].unit
    const iv = p.limite_inferior !== null && p.limite_superior !== null
      ? `<div class="gt-row"><b>Intervalo ${Math.round((d.nivel_intervalo || 0.9) * 100)} %:</b> ${fmtVal(variable, p.limite_inferior)} – ${fmtVal(variable, p.limite_superior)} ${u}</div>` : ''
    return `<div class="gt-title">${esc(p.nombre.replace('Estación sintética ', 'Estación '))} (${esc(p.clave_estacion)})</div>
      <div class="gt-row"><b>${VARS[variable].label}:</b> ${p.valor === null ? 'sin estimación' : `${fmtVal(variable, p.valor)} ${u}`}</div>${iv}
      <div class="gt-row"><b>Municipio:</b> ${esc(p.municipio)}</div><div class="gt-row"><b>Dependencia:</b> ${esc(p.dependencia)}</div>
      <div class="gt-flag" style="color:${CAL_COLOR[p.calidad] || '#667085'}">${esc(CALIDAD[p.calidad]?.label || p.calidad)}</div>
      <div class="gt-date">${esc(d.fecha)}${p.origen === 'estimado' ? ' · valor estimado, no medido' : ''}</div>`
  }

  async function refresh() {
    const r = await fetch(`${base}/api/v1/mapa?variable=${variable}${fecha ? `&fecha=${fecha}` : ''}`)
    if (!r.ok) throw new Error(`API ${r.status}`)
    const d = await r.json()
    group.clearLayers()
    d.estaciones.filter((p) => p.origen !== 'fuera_de_operacion').forEach((p) => {
      const est = p.origen === 'estimado', none = p.origen === 'no_estimable' || p.valor === null
      const icon = L.divIcon({ className: 'gt-wrap', iconSize: [38, 46], iconAnchor: [19, 44],
        html: `<div class="gt-pin ${est ? 'est' : ''} ${none ? 'none' : ''}" style="--c:${none ? '#9aa5b5' : valueColor(variable, p.valor)}"><span>${none ? 'n/e' : esc(fmtVal(variable, p.valor))}</span></div>` })
      const m = L.marker([p.latitud, p.longitud], { icon }).bindPopup(popup(p, d), { minWidth: 230 })
      if (opts.onSelect) m.on('click', () => opts.onSelect(p.clave_estacion, variable))
      group.addLayer(m)
    })
    return d
  }
  refresh().catch((e) => opts.onError && opts.onError(e))
  return {
    layer: group, refresh,
    setVariable: (v) => { variable = v; return refresh() },
    setDate: (f) => { fecha = f; return refresh() },
    remove: () => map.removeLayer(group),
  }
}
