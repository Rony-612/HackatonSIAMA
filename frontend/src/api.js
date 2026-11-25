let BASE = ''
export const setBase = (b) => { BASE = (b || '').replace(/\/$/, '') }

async function parse(r) {
  if (!r.ok) {
    let d = r.status
    try { d = (await r.json()).detail || d } catch (_) { /* sin cuerpo */ }
    throw new Error(String(d))
  }
  return r.json()
}
export const get = (path) => fetch(BASE + path).then(parse)
export const post = (path, body) =>
  fetch(BASE + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(parse)
let GEO = './geo/mexico_vecinos.json'
export const setGeoUrl = (u) => { GEO = u }
export const geo = () => fetch(GEO).then(parse)
export const downloadUrl = (f) => `${BASE}/api/v1/descargas/${f}`
export const docsUrl = () => `${BASE}/docs`
