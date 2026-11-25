import React from 'react'
import { createRoot } from 'react-dom/client'
import 'leaflet/dist/leaflet.css'
import './styles.css'
import SiamaGapfill from './SiamaGapfill.jsx'

// Uso como app:   /?mode=all|precip|temp&embed=1&station=SIM001&variable=tmax
// Uso como componente en el stack de SIAMA:  import SiamaGapfill from './SiamaGapfill'
//   <SiamaGapfill mode="precip" apiBase="https://api.siama.example" embed />
const q = new URLSearchParams(location.search)
createRoot(document.getElementById('root')).render(
  <SiamaGapfill
    mode={q.get('mode') || 'all'}
    embed={q.get('embed') === '1'}
    apiBase={q.get('api') || ''}
    initialStation={q.get('station') || null}
    initialVariable={q.get('variable') || null}
  />
)
