import { useContext } from 'react'
import { AppContext, type AppContextValue } from './AppContext'

/** Single access point for global app state. Throws outside the provider. */
export function useApp(): AppContextValue {
  const context = useContext(AppContext)
  if (context === null) {
    throw new Error('useApp() must be used inside <AppProvider> (see src/App.tsx)')
  }
  return context
}

/** For components that may render outside the provider (e.g. isolated previews). */
export function useOptionalApp(): AppContextValue | null {
  return useContext(AppContext)
}

export type { AppContextValue } from './AppContext'
export { PROTOTYPE_ROLES, type PrototypeRole } from './AppContext'
