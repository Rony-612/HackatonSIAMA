import React from 'react'
import { createRoot } from 'react-dom/client'
import 'leaflet/dist/leaflet.css'
import './styles.css'
import { setGeoUrl } from './api.js'
import SiamaGapfill from './SiamaGapfill.jsx'
import { addToLeafletMap } from './leafletLayer.js'

// Bundle para incrustar sin build en el sitio de SIAMA:
//   <link rel="stylesheet" href="…/widget/siama-gapfill.css"><script src="…/widget/siama-gapfill.js"></script>
//   SiamaGapfill.mount(document.getElementById('x'), { apiBase, mode: 'precip'|'temp'|'all', station })   // panel completo
//   SiamaGapfill.addToLeafletMap(map, { apiBase, variable })                                              // capa en el mapa existente
// el contorno estatal se busca junto al propio script (…/widget/geo/…), no relativo a la página anfitriona
const SCRIPT = document.currentScript && document.currentScript.src
if (SCRIPT) setGeoUrl(SCRIPT.replace(/[^/]*$/, 'geo/mexico_vecinos.json'))

function mount(el, opts = {}) {
  const root = createRoot(el)
  root.render(<SiamaGapfill embed mode={opts.mode || 'all'} apiBase={opts.apiBase || ''} initialStation={opts.station || null} initialVariable={opts.variable || null} />)
  return { unmount: () => root.unmount() }
}
window.SiamaGapfill = { mount, addToLeafletMap, version: '2.1.0' }
