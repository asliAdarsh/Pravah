import { useCallback, useEffect, useState } from 'react'

/**
 * Appearance preference.
 *
 * - `light`  always the light palette
 * - `dark`   always the dark palette
 * - `system` follow the operating system / device setting, live (the OS can flip
 *            at sunset and the app must follow without a reload)
 *
 * The resolved theme is written to `<html data-theme>`, which is the only switch
 * every colour token in index.css hangs off. `color-scheme` is set alongside it so
 * native scrollbars, selects and focus rings match.
 */
export type ThemePreference = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'pravah.theme'

export const THEME_OPTIONS: {
  id: ThemePreference
  label: string
  hint: string
}[] = [
  { id: 'light', label: 'Light', hint: 'Always the light industrial palette' },
  { id: 'dark', label: 'Dark', hint: 'Always the dark palette — for night shift and low-glare rigs' },
  { id: 'system', label: 'Device', hint: 'Follow this device’s appearance setting' },
]

function readStoredPreference(): ThemePreference {
  if (typeof window === 'undefined') return 'system'
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY)
  return stored === 'light' || stored === 'dark' || stored === 'system' ? stored : 'system'
}

function systemTheme(): ResolvedTheme {
  if (typeof window === 'undefined' || !window.matchMedia) return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function applyTheme(resolved: ResolvedTheme): void {
  document.documentElement.dataset.theme = resolved
}

export function useTheme() {
  const [preference, setPreferenceState] = useState<ThemePreference>(readStoredPreference)
  const [resolved, setResolved] = useState<ResolvedTheme>(() =>
    preference === 'system' ? systemTheme() : preference,
  )

  // Apply on every preference change and re-resolve whenever the device flips.
  useEffect(() => {
    if (preference === 'system') {
      const media = window.matchMedia('(prefers-color-scheme: dark)')
      const sync = () => {
        const next = systemTheme()
        setResolved(next)
        applyTheme(next)
      }
      sync()
      media.addEventListener('change', sync)
      return () => media.removeEventListener('change', sync)
    }
    setResolved(preference)
    applyTheme(preference)
    return undefined
  }, [preference])

  const setPreference = useCallback((next: ThemePreference) => {
    window.localStorage.setItem(THEME_STORAGE_KEY, next)
    setPreferenceState(next)
  }, [])

  /** Explicitly toggle between light and dark; `system` resolves first. */
  const toggle = useCallback(() => {
    setPreference(resolved === 'dark' ? 'light' : 'dark')
  }, [resolved, setPreference])

  return { preference, resolved, setPreference, toggle }
}
