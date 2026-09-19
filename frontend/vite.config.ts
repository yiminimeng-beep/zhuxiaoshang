import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 8001,
    strictPort: true,
    // 浏览器只看见 8001 一个源 → 不发跨源请求 → 8002 不必加 CORS（spec「页面承载方式」）
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
    // tests/stub-api.ts 用 vi.stubGlobal('fetch', ...) 换掉全局 fetch，
    // 用例之间必须还原，否则第一个用例的桩会漏给下一个
    unstubGlobals: true,
  },
})
