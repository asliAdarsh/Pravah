import {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { useSearchParams } from 'react-router-dom'
import { Api, ApiError } from '@/api/client'
import type {
  MetaResponse,
  RelevanceConfigPost,
  RelevanceConfigResponse,
  RelevanceWeights,
  RiskConfig,
  RiskConfigPost,
  Well,
  WellSummary,
} from '@/api/types'
import type { Tone } from '@/components/primitives'
import { useTheme, type ResolvedTheme, type ThemePreference } from '@/theme/useTheme'
import { notifyRecordsChanged } from '@/lib/useAsyncData'

export type PrototypeRole =
  | 'DRILLING_ENGINEER'
  | 'RIG_SITE'
  | 'OFFICE_ENGINEER'
  | 'GEOSCIENTIST'

export const PROTOTYPE_ROLES: { id: PrototypeRole; label: string }[] = [
  { id: 'DRILLING_ENGINEER', label: 'Drilling Engineer' },
  { id: 'RIG_SITE', label: 'Rig-site' },
  { id: 'OFFICE_ENGINEER', label: 'Office Engineer' },
  { id: 'GEOSCIENTIST', label: 'Geoscientist' },
]

const ROLE_STORAGE_KEY = 'pravah.prototype.role'

/** Prototype defaults (contract §0.5) — used until /config/relevance responds. */
const DEFAULT_WEIGHTS: RelevanceWeights = {
  formation_similarity: 0.4,
  depth_similarity: 0.3,
  spatial_proximity: 0.2,
  event_similarity: 0.1,
}

export interface ToastMessage {
  id: number
  message: string
  tone: Tone
}

export interface AppContextValue {
  meta: MetaResponse | null
  wells: WellSummary[]
  currentWell: Well | null
  currentWellLoading: boolean
  currentWellId: string
  setCurrentWell: (wellId: string) => void
  weights: RelevanceWeights
  applyRelevanceConfig: (post: RelevanceConfigPost) => Promise<void>
  applyRiskConfig: (post: RiskConfigPost) => Promise<void>
  recompute: () => Promise<void>
  setWeights: (next: RelevanceWeights) => Promise<void>
  relevanceConfig: RelevanceConfigResponse | null
  riskConfig: RiskConfig | null
  selectedOffsetWellIds: string[]
  toggleOffsetWell: (wellId: string) => void
  clearOffsetWells: () => void
  evidenceEventId: string | null
  openEvidence: (eventId: string) => void
  closeEvidence: () => void
  decisionAlertId: string | null
  openDecision: (alertId: string) => void
  closeDecision: () => void
  role: PrototypeRole
  setRole: (role: PrototypeRole) => void
  loading: boolean
  error: string | null
  apiOk: boolean | null
  toast: ToastMessage | null
  /** The one theme state instance in the app (docs/UX_CONTRACT.md §5.1). */
  theme: ThemePreference
  resolvedTheme: ResolvedTheme
  setTheme: (theme: ThemePreference) => void
  toggleTheme: () => void
  notify: (message: string, tone?: Tone) => void
  dismissToast: () => void
  refresh: () => void
}

export const AppContext = createContext<AppContextValue | null>(null)

export function AppProvider({ children }: { children: ReactNode }) {
  const [searchParams, setSearchParams] = useSearchParams()

  const [meta, setMeta] = useState<MetaResponse | null>(null)
  const [wells, setWells] = useState<WellSummary[]>([])
  const [currentWell, setCurrentWellDetail] = useState<Well | null>(null)
  const [currentWellLoading, setCurrentWellLoading] = useState(false)
  const [relevanceConfig, setRelevanceConfig] = useState<RelevanceConfigResponse | null>(null)
  const [riskConfig, setRiskConfig] = useState<RiskConfig | null>(null)
  const [selectedOffsetWellIds, setSelectedOffsetWellIds] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [apiOk, setApiOk] = useState<boolean | null>(null)
  const [toast, setToast] = useState<ToastMessage | null>(null)
  const [bootstrapNonce, setBootstrapNonce] = useState(0)
  const toastSeq = useRef(0)

  const [role, setRoleState] = useState<PrototypeRole>(() => {
    if (typeof window === 'undefined') return 'DRILLING_ENGINEER'
    const stored = window.localStorage.getItem(ROLE_STORAGE_KEY)
    const match = PROTOTYPE_ROLES.find((entry) => entry.id === stored)
    return match ? match.id : 'DRILLING_ENGINEER'
  })

  const notify = useCallback((message: string, tone: Tone = 'info') => {
    toastSeq.current += 1
    setToast({ id: toastSeq.current, message, tone })
  }, [])

  const dismissToast = useCallback(() => setToast(null), [])

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 4200)
    return () => clearTimeout(timer)
  }, [toast])

  /* ---------------------------- bootstrap ---------------------------- */

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    ;(async () => {
      try {
        const [metaResponse, wellsResponse, relevance, risk] = await Promise.all([
          Api.meta(controller.signal),
          Api.wells({}, controller.signal),
          Api.relevanceConfig(controller.signal).catch(() => null),
          Api.riskConfig(controller.signal).catch(() => null),
        ])
        if (controller.signal.aborted) return
        setMeta(metaResponse)
        setWells(wellsResponse.items)
        if (relevance) setRelevanceConfig(relevance)
        if (risk) setRiskConfig(risk)
        setApiOk(true)
        setError(null)
      } catch (cause) {
        if (controller.signal.aborted) return
        setApiOk(false)
        setError(
          cause instanceof ApiError
            ? cause.message
            : 'Could not reach the API. Start the backend on 127.0.0.1:8000.',
        )
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    })()
    return () => controller.abort()
  }, [bootstrapNonce])

  const currentWellId = useMemo(() => {
    const fromUrl = searchParams.get('well')
    if (fromUrl) return fromUrl
    if (meta?.demo.current_well_id) return meta.demo.current_well_id
    return wells[0]?.id ?? ''
  }, [searchParams, meta, wells])

  /* -------------------------- current well --------------------------- */

  useEffect(() => {
    if (!currentWellId) {
      setCurrentWellDetail(null)
      setCurrentWellLoading(false)
      return
    }
    const controller = new AbortController()
    setCurrentWellDetail(null)
    setCurrentWellLoading(true)
    Api.well(currentWellId, controller.signal)
      .then((detail) => {
        if (!controller.signal.aborted) setCurrentWellDetail(detail)
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return
        notify(
          cause instanceof ApiError ? cause.message : `Could not load well ${currentWellId}`,
          'critical',
        )
      })
      .finally(() => {
        if (!controller.signal.aborted) setCurrentWellLoading(false)
      })
    return () => controller.abort()
  }, [currentWellId, notify])

  /* ------------------------- URL-backed state ------------------------ */

  const setParam = useCallback(
    (key: string, value: string | null) => {
      setSearchParams(
        (previous) => {
          const next = new URLSearchParams(previous)
          if (value === null) next.delete(key)
          else next.set(key, value)
          return next
        },
        { replace: false },
      )
    },
    [setSearchParams],
  )

  const setCurrentWell = useCallback((wellId: string) => setParam('well', wellId), [setParam])

  const evidenceEventId = searchParams.get('event')
  const decisionAlertId = searchParams.get('alert')

  const openEvidence = useCallback((eventId: string) => setParam('event', eventId), [setParam])
  const closeEvidence = useCallback(() => setParam('event', null), [setParam])
  const openDecision = useCallback((alertId: string) => setParam('alert', alertId), [setParam])
  const closeDecision = useCallback(() => setParam('alert', null), [setParam])

  const toggleOffsetWell = useCallback((wellId: string) => {
    setSelectedOffsetWellIds((previous) =>
      previous.includes(wellId) ? previous.filter((id) => id !== wellId) : [...previous, wellId],
    )
  }, [])

  const clearOffsetWells = useCallback(() => setSelectedOffsetWellIds([]), [])

  const setRole = useCallback((next: PrototypeRole) => {
    setRoleState(next)
    if (typeof window !== 'undefined') window.localStorage.setItem(ROLE_STORAGE_KEY, next)
  }, [])

  /* --------------------------- weights ------------------------------- */

  const recompute = useCallback(async () => {
    try {
      const result = await Api.recompute(currentWellId)
      setApiOk(true)
      notifyRecordsChanged()
      notify(
        `Records recomputed · ${result.offset_relations_updated} offset relation(s), ${result.alerts_created} alert(s) created`,
        'success',
      )
    } catch (cause) {
      notify(
        cause instanceof ApiError ? cause.message : 'Could not recompute records',
        'critical',
      )
      throw cause
    }
  }, [currentWellId, notify])

  const applyRelevanceConfig = useCallback(
    async (post: RelevanceConfigPost) => {
      try {
        const saved = await Api.setRelevanceConfig(post)
        setRelevanceConfig(saved)
        await recompute()
      } catch (cause) {
        notify(
          cause instanceof ApiError ? cause.message : 'Could not persist the relevance configuration',
          'critical',
        )
        throw cause
      }
    },
    [recompute, notify],
  )

  const applyRiskConfig = useCallback(
    async (post: RiskConfigPost) => {
      try {
        const saved = await Api.setRiskConfig(post)
        setRiskConfig(saved)
        notifyRecordsChanged()
        notify('Risk rule persisted · alerts recomputed for every active well', 'success')
      } catch (cause) {
        notify(
          cause instanceof ApiError ? cause.message : 'Could not persist the risk configuration',
          'critical',
        )
        throw cause
      }
    },
    [notify],
  )

  const setWeights = useCallback(
    async (next: RelevanceWeights) => {
      if (!meta) return
      await applyRelevanceConfig({
        weights: next,
        radius_km: relevanceConfig?.radius_km ?? 8,
        min_relevance: relevanceConfig?.min_relevance ?? 0.15,
      })
    },
    [meta, relevanceConfig, applyRelevanceConfig],
  )

  const refresh = useCallback(() => setBootstrapNonce((value) => value + 1), [])

  const { preference: theme, resolved: resolvedTheme, setPreference: setTheme, toggle: toggleTheme } =
    useTheme()

  const value = useMemo<AppContextValue>(
    () => ({
      meta,
      wells,
      currentWell,
      currentWellLoading,
      currentWellId,
      setCurrentWell,
      weights: relevanceConfig?.weights ?? DEFAULT_WEIGHTS,
      setWeights,
      relevanceConfig,
      riskConfig,
      applyRelevanceConfig,
      applyRiskConfig,
      recompute,
      selectedOffsetWellIds,
      toggleOffsetWell,
      clearOffsetWells,
      evidenceEventId,
      openEvidence,
      closeEvidence,
      decisionAlertId,
      openDecision,
      closeDecision,
      role,
      setRole,
      loading,
      error,
      apiOk,
      toast,
      notify,
      dismissToast,
      refresh,
      theme,
      resolvedTheme,
      setTheme,
      toggleTheme,
    }),
    [
      meta,
      wells,
      currentWell,
      currentWellLoading,
      currentWellId,
      setCurrentWell,
      setWeights,
      relevanceConfig,
      applyRelevanceConfig,
      applyRiskConfig,
      recompute,
      riskConfig,
      selectedOffsetWellIds,
      toggleOffsetWell,
      clearOffsetWells,
      evidenceEventId,
      openEvidence,
      closeEvidence,
      decisionAlertId,
      openDecision,
      closeDecision,
      role,
      setRole,
      loading,
      error,
      apiOk,
      theme,
      resolvedTheme,
      setTheme,
      toggleTheme,
      toast,
      notify,
      dismissToast,
      refresh,
    ],
  )

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}
