import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// El build se escribe en ../web, que es lo que sirve FastAPI. Rutas relativas: funciona bajo cualquier prefijo.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: { outDir: '../web', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:8000', '/health': 'http://127.0.0.1:8000' } },
})
