import { useCallback, useEffect, useRef, useState } from 'react'

export interface AsyncState<T> {
  data: T | null
  error: string | null
  loading: boolean
  reload: () => void
}

/**
 * Broadcast by a writer (e.g. the decision panel after an acknowledge or a
 * note) so every mounted list re-reads. Without it the alert centre keeps
 * showing OPEN behind the panel that just acknowledged the alert.
 */
export const RECORDS_CHANGED_EVENT = 'pravah:records-changed'

export function notifyRecordsChanged(): void {
  window.dispatchEvent(new Event(RECORDS_CHANGED_EVENT))
}

/**
 * Small request-state hook for prototype screens: aborts in-flight requests on
 * dependency change / unmount, surfaces a human-readable error string, and
 * keeps the previous payload visible while a reload is in flight.
 */
export function useAsyncData<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    fetcherRef
      .current(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return
        setData(result)
        setError(null)
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return
        setError(
          cause instanceof Error
            ? cause.message
            : 'Unexpected request failure',
        )
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [...deps, nonce])

  // A record was written elsewhere (acknowledge, note, ingest): re-read so no
  // screen keeps showing a status that no longer exists.
  useEffect(() => {
    const onRecordsChanged = () => setNonce((value) => value + 1)
    window.addEventListener(RECORDS_CHANGED_EVENT, onRecordsChanged)
    return () => window.removeEventListener(RECORDS_CHANGED_EVENT, onRecordsChanged)
  }, [])

  const reload = useCallback(() => setNonce((value) => value + 1), [])

  return { data, error, loading, reload }
}
