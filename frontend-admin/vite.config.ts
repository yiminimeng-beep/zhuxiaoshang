import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

/**
 * 后台（8002 的 `/admin/*`）是**独立工程**，与商家/用户端（8001）只共用
 * `tokens.css` 一套设计 token。
 *
 * `base: '/admin/'` 是生产期的落脚点：构建产物被 8002 的 FastAPI 挂在 `/admin`，
 * 同源，proxy 自然消失。开发期 Vite 会把根路径也挪到 `/admin/`，
 * 所以本地起的是 `http://localhost:8003/admin/`。
 */
export default defineConfig({
  base: '/admin/',
  plugins: [react(), tailwindcss()],
  server: {
    port: 8003,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://localhost:8002', changeOrigin: false },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    restoreMocks: true,
    unstubGlobals: true,
  },
})
