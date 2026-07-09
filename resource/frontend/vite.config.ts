import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react-oxc'
import tailwindcss from '@tailwindcss/vite'

// No version define: the frontend reads the plugin version at runtime from
// /api/mode, keeping the committed dist/ bundle stable across version bumps.
// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: process.env.MODE === 'production'
    ? '/plugins/admin-toolkit/resource/dist/'
    : '/',
  build: {
    outDir: '../../resource/dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name].js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name].[ext]'
      }
    }
  }
})
