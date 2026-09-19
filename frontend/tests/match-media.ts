/** 测试里驱动客户壳宽/窄布局（与 `useWideViewport` 对齐） */

export function mockMatchMedia(wide: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string) => {
      const matches = wide && String(query).includes('40rem')
      return {
        matches,
        media: query,
        onchange: null,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        dispatchEvent: () => false,
      }
    },
  })
}
