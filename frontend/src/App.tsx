import { useEffect } from 'react'
import { BrowserRouter, Route, Routes, useParams, useSearchParams } from 'react-router-dom'
import { AppShell } from '@/components/AppShell'
import { AppProvider } from '@/store/AppContext'

import { Dashboard } from '@/pages/Dashboard'
import { Documents } from '@/pages/Documents'
import { Alerts } from '@/pages/Alerts'
import { OffsetMap } from '@/pages/OffsetMap'
import { OffsetReplay } from '@/pages/OffsetReplay'
import { Timeline } from '@/pages/Timeline'
import { Search } from '@/pages/Search'
import { Settings } from '@/pages/Settings'
import { TelemetryMonitor } from '@/pages/TelemetryMonitor'
import { Validation } from '@/pages/Validation'
import { RelevanceMethod } from '@/pages/RelevanceMethod'
import { GraphExplorer } from '@/pages/GraphExplorer'
import { NotFound } from '@/pages/NotFound'

/**
 * `/alerts/:alertId` opens the globally mounted DecisionPanel. The route
 * parameter is mirrored into the `?alert=` query param so the panel state has a
 * single source of truth and stays deep-linkable.
 */
function AlertRouteBridge() {
  const { alertId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  useEffect(() => {
    if (!alertId || searchParams.get('alert') === alertId) return
    const next = new URLSearchParams(searchParams)
    next.set('alert', alertId)
    setSearchParams(next, { replace: true })
  }, [alertId, searchParams, setSearchParams])
  return null
}

export function App() {
  return (
    <BrowserRouter>
      <AppProvider>
        <AppShell>
          <AlertRouteBridge />
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/offset-intelligence/map" element={<OffsetMap />} />
            <Route path="/offset-intelligence/replay" element={<OffsetReplay />} />
            <Route path="/events/timeline" element={<Timeline />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/alerts/:alertId" element={<Alerts />} />
            <Route path="/search" element={<Search />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/telemetry" element={<TelemetryMonitor />} />
            <Route path="/validation" element={<Validation />} />
            <Route path="/relevance-method" element={<RelevanceMethod />} />
            <Route path="/graph" element={<GraphExplorer />} />
            <Route path="/documents" element={<Documents />} />
            <Route path="/documents/:docId" element={<Documents />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </AppShell>
      </AppProvider>
    </BrowserRouter>
  )
}
