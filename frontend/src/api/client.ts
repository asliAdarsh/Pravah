import type {
  AlertsResponse,
  DocumentDetail,
  DocumentsResponse,
  EventsResponse,
  EvidenceResponse,
  FormationsResponse,
  HealthResponse,
  IngestRequest,
  IngestResponse,
  MetaResponse,
  NearbyResponse,
  NoteRequest,
  NoteResponse,
  AlertStatusResponse,
  AlertDetail,
  AlertStatusRequest,
  ReplayPayload,
  RecomputeResponse,
  RelevanceConfigPost,
  RelevanceConfigResponse,
  RiskConfigPost,
  RiskConfig,
  SearchRequest,
  SearchResponse,
  TimelineResponse,
  Well,
  WellEventsResponse,
  WellsResponse,
  DemoScenarioResponse,
} from './types'

/**
 * Dev server proxies `/api` → http://127.0.0.1:8000 (see vite.config.ts), so the
 * default base is a relative `/api`. `VITE_API_BASE` overrides it for deployments
 * that serve the API from another origin.
 */
export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(
  /\/$/,
  '',
) ?? '/api'

const DEFAULT_TIMEOUT_MS = 20_000

export class ApiError extends Error {
  readonly status: number
  readonly path: string
  readonly detail: unknown

  constructor(path: string, status: number, detail: unknown) {
    super(formatDetail(detail) ?? `Request failed (${status})`)
    this.name = 'ApiError'
    this.status = status
    this.path = path
    this.detail = detail
  }

  /** True when the backend could not be reached at all (offline / not started). */
  get isNetworkError(): boolean {
    return this.status === 0
  }
}

function formatDetail(detail: unknown): string | null {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) =>
        typeof item === 'object' && item !== null && 'msg' in item
          ? String((item as { msg: unknown }).msg)
          : JSON.stringify(item),
      )
      .filter(Boolean)
    return parts.length ? parts.join('; ') : null
  }
  if (detail && typeof detail === 'object' && 'detail' in detail) {
    return formatDetail((detail as { detail: unknown }).detail)
  }
  return null
}

export type QueryParams = Record<
  string,
  string | number | boolean | null | undefined | (string | number)[]
>

function buildQuery(params?: QueryParams): string {
  if (!params) return ''
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue
    if (Array.isArray(value)) {
      if (value.length) search.set(key, value.join(','))
    } else {
      search.set(key, String(value))
    }
  }
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

interface RequestOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  params?: QueryParams
  signal?: AbortSignal
  timeoutMs?: number
  formData?: FormData
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, params, signal, timeoutMs = DEFAULT_TIMEOUT_MS, formData } =
    options
  const url = `${API_BASE}${path}${buildQuery(params)}`

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(new DOMException('Timeout', 'TimeoutError')), timeoutMs)
  const onExternalAbort = () => controller.abort(signal?.reason)
  if (signal) {
    if (signal.aborted) onExternalAbort()
    else signal.addEventListener('abort', onExternalAbort, { once: true })
  }

  let response: Response
  try {
    response = await fetch(url, {
      method,
      signal: controller.signal,
      headers: formData
        ? undefined
        : body !== undefined
          ? { 'Content-Type': 'application/json' }
          : undefined,
      body: formData ?? (body !== undefined ? JSON.stringify(body) : undefined),
    })
  } catch (cause) {
    if (signal?.aborted) throw new ApiError(path, 0, 'Request cancelled')
    if (cause instanceof DOMException && cause.name === 'TimeoutError') {
      throw new ApiError(path, 0, `Timed out after ${timeoutMs} ms — is the API running?`)
    }
    throw new ApiError(path, 0, 'API unreachable — is the backend running on 127.0.0.1:8000?')
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onExternalAbort)
  }

  if (response.status === 204) return undefined as T

  const raw = await response.text()
  let parsed: unknown = null
  if (raw.length) {
    try {
      parsed = JSON.parse(raw)
    } catch {
      parsed = raw
    }
  }

  if (!response.ok) throw new ApiError(path, response.status, parsed)
  return parsed as T
}

export const api = {
  get: <T>(path: string, params?: QueryParams, signal?: AbortSignal): Promise<T> =>
    request<T>(path, { params, signal }),
  post: <T>(path: string, body?: unknown, params?: QueryParams, signal?: AbortSignal): Promise<T> =>
    request<T>(path, { method: 'POST', body, params, signal }),
  postForm: <T>(path: string, formData: FormData, signal?: AbortSignal): Promise<T> =>
    request<T>(path, { method: 'POST', formData, signal }),
}

/* ------------------------------------------------------------------ *
 * Endpoint helpers — every path below exists in docs/API_CONTRACT.md.
 * ------------------------------------------------------------------ */

export const Api = {
  health: (signal?: AbortSignal) => api.get<HealthResponse>('/v1/health', undefined, signal),
  meta: (signal?: AbortSignal) => api.get<MetaResponse>('/v1/meta', undefined, signal),

  formations: (signal?: AbortSignal) => api.get<FormationsResponse>('/v1/formations', undefined, signal),

  wells: (
    params: { status?: string; field?: string; q?: string; limit?: number; offset?: number } = {},
    signal?: AbortSignal,
  ) => api.get<WellsResponse>('/v1/wells', params, signal),
  well: (wellId: string, signal?: AbortSignal) => api.get<Well>(`/v1/wells/${wellId}`, undefined, signal),
  nearby: (
    wellId: string,
    params: {
      radius_km?: number
      min_relevance?: number
      event_type?: string
      formation?: string
      status?: string
      depth_min_md?: number
      depth_max_md?: number
    } = {},
    signal?: AbortSignal,
  ) => api.get<NearbyResponse>(`/v1/wells/${wellId}/nearby`, params, signal),
  wellEvents: (
    wellId: string,
    params: { event_type?: string; tvd_min?: number; tvd_max?: number; limit?: number } = {},
    signal?: AbortSignal,
  ) => api.get<WellEventsResponse>(`/v1/wells/${wellId}/events`, params, signal),
  timeline: (
    wellId: string,
    params: { window_md?: number; event_type?: string } = {},
    signal?: AbortSignal,
  ) => api.get<TimelineResponse>(`/v1/wells/${wellId}/timeline`, params, signal),

  events: (
    params: {
      well_id?: string
      event_type?: string
      formation?: string
      tvd_min?: number
      tvd_max?: number
      severity_min?: string
      near_well_id?: string
      radius_km?: number
      limit?: number
      offset?: number
    } = {},
    signal?: AbortSignal,
  ) => api.get<EventsResponse>('/v1/events', params, signal),
  event: (eventId: string, signal?: AbortSignal) =>
    api.get<EvidenceResponse & { correlated_events?: unknown[] }>(
      `/v1/events/${eventId}`,
      undefined,
      signal,
    ),
  evidence: (eventId: string, signal?: AbortSignal) =>
    api.get<EvidenceResponse>(`/v1/evidence/${eventId}`, undefined, signal),

  documents: (
    params: { well_id?: string; doc_type?: string; limit?: number } = {},
    signal?: AbortSignal,
  ) => api.get<DocumentsResponse>('/v1/documents', params, signal),
  document: (docId: string, signal?: AbortSignal) =>
    api.get<DocumentDetail>(`/v1/documents/${docId}`, undefined, signal),
  ingest: (body: IngestRequest, signal?: AbortSignal) =>
    api.post<IngestResponse>('/v1/documents/ingest', body, undefined, signal),
  ingestFile: (formData: FormData, signal?: AbortSignal) =>
    api.postForm<IngestResponse>('/v1/documents/ingest', formData, signal),

  replay: (
    wellId: string,
    params: {
      radius_km?: number
      event_type?: string
      top_tvd?: number
      bottom_tvd?: number
      limit_wells?: number
    } = {},
    signal?: AbortSignal,
  ) => api.get<ReplayPayload>(`/v1/offset-replay/${wellId}`, params, signal),

  alerts: (
    params: { well_id?: string; status?: string; severity?: string } = {},
    signal?: AbortSignal,
  ) => api.get<AlertsResponse>('/v1/alerts', params, signal),
  alert: (alertId: string, signal?: AbortSignal) =>
    api.get<AlertDetail>(`/v1/alerts/${alertId}`, undefined, signal),
  acknowledge: (alertId: string, body: { engineer: string; note: string }, signal?: AbortSignal) =>
    api.post<AlertStatusResponse>(`/v1/alerts/${alertId}/acknowledge`, body, undefined, signal),
  addNote: (alertId: string, body: NoteRequest, signal?: AbortSignal) =>
    api.post<NoteResponse>(`/v1/alerts/${alertId}/notes`, body, undefined, signal),
  setStatus: (alertId: string, body: AlertStatusRequest, signal?: AbortSignal) =>
    api.post<AlertStatusResponse>(`/v1/alerts/${alertId}/status`, body, undefined, signal),

  search: (body: SearchRequest, signal?: AbortSignal) =>
    api.post<SearchResponse>('/v1/search', body, undefined, signal),

  relevanceConfig: (signal?: AbortSignal) =>
    api.get<RelevanceConfigResponse>('/v1/config/relevance', undefined, signal),
  setRelevanceConfig: (body: RelevanceConfigPost, signal?: AbortSignal) =>
    api.post<RelevanceConfigResponse>('/v1/config/relevance', body, undefined, signal),
  riskConfig: (signal?: AbortSignal) => api.get<RiskConfig>('/v1/config/risk', undefined, signal),
  setRiskConfig: (body: RiskConfigPost, signal?: AbortSignal) =>
    api.post<RiskConfig>('/v1/config/risk', body, undefined, signal),
  recompute: (wellId: string, signal?: AbortSignal) =>
    api.post<RecomputeResponse>('/v1/risk/recompute', { well_id: wellId }, undefined, signal),

  demoScenario: (signal?: AbortSignal) =>
    api.post<DemoScenarioResponse>('/v1/demo/scenario', undefined, undefined, signal),
}
