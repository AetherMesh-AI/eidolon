import { act } from '@testing-library/react'
import { vi } from 'vitest'

/** Supply actual content-box observations, independently of jsdom's viewport. */
export function observeMasterDetailWidth() {
  const callbacks = new Set<ResizeObserverCallback>()
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(private callback: ResizeObserverCallback) {}
      observe() {
        callbacks.add(this.callback)
      }
      disconnect() {
        callbacks.delete(this.callback)
      }
    }
  )

  return (width: number) =>
    act(() => {
      for (const callback of callbacks) {
        callback([{ contentRect: { width } } as ResizeObserverEntry], {} as ResizeObserver)
      }
    })
}
