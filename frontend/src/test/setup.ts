import '@testing-library/jest-dom/vitest'
import { vi } from 'vitest'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

// jsdom 在本机环境下不暴露 localStorage（window.localStorage 为 undefined），
// 依赖偏好读写的用例先补一个内存实现，避免整批测试因环境缺件失败。
if (typeof window !== 'undefined' && !window.localStorage) {
  const store = new Map<string, string>()
  const memory: Storage = {
    get length() {
      return store.size
    },
    key: (i) => [...store.keys()][i] ?? null,
    getItem: (k) => (store.has(k) ? (store.get(k) as string) : null),
    setItem: (k, v) => void store.set(k, String(v)),
    removeItem: (k) => void store.delete(k),
    clear: () => store.clear(),
  }
  Object.defineProperty(window, 'localStorage', { value: memory, configurable: true, writable: true })
  Object.defineProperty(globalThis, 'localStorage', { value: memory, configurable: true, writable: true })
}
