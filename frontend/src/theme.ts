import { useSyncExternalStore } from 'react'

export type Theme = 'dark' | 'light'
const KEY = 'ai-platform.theme'
const listeners = new Set<() => void>()

/** The saved choice wins; with none saved the original dark look is used. */
export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'
}

export function setTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme
  try { localStorage.setItem(KEY, theme) } catch { /* storage blocked: the choice lasts until reload */ }
  listeners.forEach((l) => l())
}

export function useTheme(): Theme {
  return useSyncExternalStore(
    (cb) => { listeners.add(cb); return () => { listeners.delete(cb) } },
    currentTheme,
    () => 'dark',
  )
}
