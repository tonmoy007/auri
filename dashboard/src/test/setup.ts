import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Testing Library only unmounts between tests by itself when test globals are
// on; they are off here, so do it explicitly.
afterEach(() => {
  cleanup()
  localStorage.clear()
  sessionStorage.clear()
})

// jsdom has no matchMedia; theme and toast libraries ask for it on mount.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }),
})
