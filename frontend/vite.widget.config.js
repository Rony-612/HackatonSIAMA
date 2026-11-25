import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Bundle autocontenido (IIFE) para incrustar en SIGWEB. Se escribe en ../web/widget sin borrar el build de la app.
export default defineConfig({
  plugins: [react()],
  define: { 'process.env.NODE_ENV': '"production"' },
  build: {
    outDir: '../web/widget', emptyOutDir: true, copyPublicDir: false, cssCodeSplit: false,
    lib: { entry: 'src/widget.jsx', name: 'SiamaGapfillBundle', formats: ['iife'], fileName: () => 'siama-gapfill.js' },
    rollupOptions: { output: { assetFileNames: 'siama-gapfill.[ext]' } },
  },
})
