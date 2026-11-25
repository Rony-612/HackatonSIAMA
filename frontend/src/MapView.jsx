import React, { useEffect, useMemo, useState } from 'react'
import { MapContainer, TileLayer, GeoJSON, CircleMarker, Marker, Popup, Tooltip, useMap } from 'react-leaflet'
import L from 'leaflet'
import { geo } from './api.js'
import { gapColor, valueColor, fmtVal, VARS, CALIDAD } from './ui.js'

function Fit({ bounds }) {
  const map = useMap()
  useEffect(() => {
    if (bounds?.isValid()) map.fitBounds(bounds, { padding: [14, 14] })
  }, [bounds, map])
  return null
}

const esc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))

// Pin al estilo del Monitor de Clima: globo con el valor. Borde sólido = observado; punteado = estimado; gris = no estimable.
function pinIcon(variable, p, sel) {
  const est = p.origen === 'estimado'
  const none = p.origen === 'no_estimable' || p.valor === null
  const c = none ? '#9aa5b5' : valueColor(variable, p.valor)
  const cls = `pin ${est ? 'est' : ''} ${none ? 'none' : ''} ${sel ? 'sel' : ''}`
  return L.divIcon({ className: 'pin-wrap', iconSize: [38, 46], iconAnchor: [19, 44],
    html: `<div class="${cls}" style="--c:${c}"><span>${none ? 'n/e' : esc(fmtVal(variable, p.valor))}</span></div>` })
}

export default function MapView({ stations, selected, onSelect, layer = 'vacios', day = null, variable = 'tmax' }) {
  const [gj, setGj] = useState(null)
  const [tiles, setTiles] = useState(true)
  useEffect(() => { geo().then(setGj).catch(() => setGj(null)) }, [])

  const gto = useMemo(() => gj?.features.find((f) => f.properties.estado), [gj])
  const bounds = useMemo(() => (gto ? L.geoJSON(gto).getBounds() : null), [gto])
  const neighbours = useMemo(() => gj && { ...gj, features: gj.features.filter((f) => !f.properties.estado) }, [gj])

  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim() || undefined

  return (
    <div style={{ position: 'relative' }}>
      <MapContainer className="map" center={[20.9, -101.0]} zoom={8} zoomControl scrollWheelZoom={false} attributionControl>
        {tiles && (
          <TileLayer
            attribution='&copy; OpenStreetMap, &copy; CARTO'
            url="https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png"
          />
        )}
        {neighbours && (
          <GeoJSON
            key="vecinos"
            data={neighbours}
            style={() => ({ color: '#8795a8', weight: 1, fillColor: css('--map-land') || '#f6f8fb', fillOpacity: tiles ? 0.35 : 1 })}
            onEachFeature={(f, layer) => layer.bindTooltip(f.properties.name, { permanent: true, direction: 'center', className: 'state-label' })}
          />
        )}
        {gto && (
          <GeoJSON
            key="gto"
            data={gto}
            style={() => ({ color: '#1d4ed8', weight: 2.4, fillColor: css('--map-state') || '#fff6e3', fillOpacity: tiles ? 0.28 : 1 })}
            onEachFeature={(f, layer) => layer.bindTooltip('Guanajuato', { permanent: true, direction: 'center', className: 'state-label' })}
          />
        )}
        <Fit bounds={bounds} />
        {layer === 'dia' && day?.estaciones.filter((p) => p.origen !== 'fuera_de_operacion').map((p) => (
          <Marker key={p.clave_estacion} position={[p.latitud, p.longitud]} icon={pinIcon(variable, p, p.clave_estacion === selected)}
                  eventHandlers={{ click: () => onSelect(p.clave_estacion) }}>
            <Popup className="sig-pop" minWidth={230}>
              <div className="sig-title">{p.nombre.replace('Estación sintética ', 'Estación ')} ({p.clave_estacion})</div>
              <div className="sig-row"><b>{VARS[variable].label}:</b> {p.valor === null ? 'sin estimación' : `${fmtVal(variable, p.valor)} ${VARS[variable].unit}`}</div>
              {p.limite_inferior !== null && p.limite_superior !== null && (
                <div className="sig-row"><b>Intervalo {Math.round((day.nivel_intervalo || 0.9) * 100)} %:</b> {fmtVal(variable, p.limite_inferior)} – {fmtVal(variable, p.limite_superior)} {VARS[variable].unit}</div>
              )}
              <div className="sig-row"><b>Municipio:</b> {p.municipio}</div>
              <div className="sig-row"><b>Dependencia:</b> {p.dependencia}</div>
              <div className="sig-flag" style={{ color: CALIDAD[p.calidad]?.color }}>{CALIDAD[p.calidad]?.label || p.calidad}</div>
              <div className="sig-date">{day.fecha}{p.origen === 'estimado' ? ' · valor estimado, no medido' : ''}</div>
            </Popup>
          </Marker>
        ))}
        {layer === 'vacios' && stations?.map((s) => {
          const sel = s.clave_estacion === selected
          return (
            <React.Fragment key={s.clave_estacion}>
              {s.cambio_abrupto && (
                <CircleMarker center={[s.latitud, s.longitud]} radius={17} pathOptions={{ color: '#d64545', weight: 2, dashArray: '3 4', fill: false }} interactive={false} />
              )}
              <CircleMarker
                center={[s.latitud, s.longitud]}
                radius={sel ? 12 : 9}
                pathOptions={{ color: sel ? '#0f1b2d' : '#ffffff', weight: sel ? 3 : 1.5, fillColor: gapColor(s.porcentaje_vacios), fillOpacity: 0.95 }}
                eventHandlers={{ click: () => onSelect(s.clave_estacion) }}
              >
                <Tooltip direction="top" offset={[0, -8]} className="st-tip">
                  <b>{s.clave_estacion}</b> · {s.municipio}<br />
                  {s.porcentaje_vacios?.toFixed(1)} % de vacíos · hueco máx. {s.hueco_mas_largo} d
                </Tooltip>
              </CircleMarker>
            </React.Fragment>
          )
        })}
      </MapContainer>
      <label className="toggle" style={{ position: 'absolute', right: 12, bottom: 12, zIndex: 500, background: 'var(--panel)', padding: '4px 8px', borderRadius: 8, border: '1px solid var(--line)' }}>
        <input type="checkbox" checked={tiles} onChange={(e) => setTiles(e.target.checked)} /> Mapa base
      </label>
    </div>
  )
}
