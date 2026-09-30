/**
 * Typed access to the four engines added with the real public dataset:
 * telemetry (MWD / mud-logger), the detector backtest, the AHP relevance
 * profiles and the knowledge graph.
 *
 * These endpoints are not in `src/api/client.ts` (that module is owned by the
 * API layer), so the request helpers and the response shapes live here, next to
 * the only screens that consume them. Everything goes through the shared `api`
 * client, so the dev proxy, the timeout and the `ApiError` message shaping are
 * identical to the rest of the app.
 *
 * Well ids contain a slash (`15/9-F-9A`). Every path built here percent-encodes
 * the id, so a well id is never concatenated raw into a URL.
 */
import { api } from '@/api/client'

/* ------------------------------------------------------------------ *
 * Telemetry — real MWD / mud-logger measurements
 * ------------------------------------------------------------------ */

export interface TelemetryChannelInfo {
  channel: string
  unit: string
  samples: number
  first_row_index: number
  last_row_index: number
  min_md: number
  max_md: number
  watched_for_hazards: string[]
}

export interface TelemetryChannels {
  well_id: string
  rows: number
  channel_count: number
  total_channel_samples: number
  depth_range_md: [number, number]
  channels: TelemetryChannelInfo[]
  unwatched_channel_count: number
  note: string
}

export interface TelemetrySnapshotChannel {
  channel: string
  unit: string
  value: number
  md: number
  row_index: number
  /** True when the newest reading is not from the anchor sample. */
  stale: boolean
  hazards: string[]
  last_alert: unknown
}

export interface TelemetrySnapshot {
  well_id: string
  well_name: string
  current_depth_md: number
  at_row: number
  anchor_row_index: number
  anchor_md: number
  channels: TelemetrySnapshotChannel[]
  channels_without_data: string[]
  note: string
}

export interface TelemetryStreamPoint {
  row_index: number
  md: number
  tvd: number
  /** Absent keys were NOT measured at this row. Never treated as zero. */
  channels: Record<string, number | null>
}

export interface TelemetryStream {
  well_id: string
  points: TelemetryStreamPoint[]
  channels: string[]
  from_row: number
  to_row: number
  max_points: number
  rows_in_window: number
  points_returned: number
  method: string
  buckets: number
  depth_range_md: [number, number]
  dropped_channels: string[]
  note: string
}

export type TelemetrySeverity = 'MODERATE' | 'HIGH' | 'CRITICAL'

export interface TelemetryAlert {
  well_id: string
  row_index: number
  md: number
  hazard: string
  channel: string
  detector: string
  severity: TelemetrySeverity
  value: number
  baseline_mean: number
  baseline_std: number
  z_score: number
  cusum_s_plus: number
  cusum_s_minus: number
  threshold: number
  message: string
}

export interface TelemetryChannelRun {
  channel: string
  samples: number
  alerts: number
  first_row_index: number
  last_row_index: number
  skipped_reason: string | null
}

export interface TelemetryAlerts {
  well_id: string
  hazard: string
  window: number
  channels_watched: string[]
  channels_evaluated: TelemetryChannelRun[]
  channels_skipped: unknown[]
  gap_policy: string
  total: number
  returned: number
  alerts: TelemetryAlert[]
}

/* ------------------------------------------------------------------ *
 * Backtest / validation
 * ------------------------------------------------------------------ */

export interface BacktestIncident {
  event_id: string
  hazard: string
  depth_md: number
  row_index: number
  source: string
}

export interface BacktestPrecision {
  lower: number
  centre: number
  upper: number
  successes: number
  trials: number
}

export interface BacktestRop {
  mean_m_per_h: number
  median_m_per_h: number
  samples: number
  from_md: number
  to_md: number
  available: boolean
  note: string
}

export interface BacktestLeakageItem {
  key: string
  label: string
  passed: boolean
  detail: string
}

export interface BacktestLeakageAudit {
  zero_future_leakage: boolean
  chronological_order_preserved: boolean
  rolling_statistics_causal: boolean
  cusum_state_recursive: boolean
  target_well_excluded_from_own_analogs: boolean
  historical_events_bounded_by_current_depth: boolean
  tests_passed: string
  truncation_check: Record<string, number | boolean | string | null>
  notes: string[]
}

export interface BacktestRun {
  id: string
  well_id: string
  hazard: string
  window: number
  incident: BacktestIncident
  alerts_total: number
  alerts_before_incident: number
  alerts_by_detector: Record<string, number>
  alerts_by_severity: Record<string, number>
  first_precursor_md: number | null
  first_precursor_row_index: number | null
  first_precursor_channel: string | null
  first_critical_md: number | null
  first_sustained_md: number | null
  sustained_run_length: number
  max_critical_run: number
  lead_distance_m: number | null
  lead_minutes: number | null
  lead_note: string
  observed_rop: BacktestRop
  precision: BacktestPrecision
  episodes_total: number
  episodes_before_incident: number
  precision_definition: string
  channels_evaluated: TelemetryChannelRun[]
  channels_skipped: unknown[]
  gap_policy: string
  leakage_audit: BacktestLeakageAudit
}

/* ------------------------------------------------------------------ *
 * AHP relevance profiles
 * ------------------------------------------------------------------ */

export interface AhpProfile {
  hazard: string
  label: string
  features: string[]
  matrix: number[][]
  weights: Record<string, number>
  consistency_ratio: number
  lambda_max: number
  cr_threshold: number
  consistent: boolean
  method: string
  engineering_rationale: string
  references: string[]
}

export interface AhpProfileIndexEntry {
  hazard: string
  label: string
  features: string[]
  weights: Record<string, number>
  consistency_ratio: number
  lambda_max: number
  consistent: boolean
}

export interface AhpProfiles {
  profiles: AhpProfileIndexEntry[]
  count: number
  general_hazard: string
  cr_threshold: number
  method: string
}

/* ------------------------------------------------------------------ *
 * Knowledge graph
 * ------------------------------------------------------------------ */

export type GraphNodeType =
  | 'Well'
  | 'Formation'
  | 'Event'
  | 'Hazard'
  | 'Intervention'
  | 'Outcome'
  | 'ReportSnippet'

export interface GraphNode {
  id: string
  type: GraphNodeType
  label: string
  [key: string]: unknown
}

export interface GraphEdge {
  source: string
  target: string
  type: string
  basis?: string
}

export interface GraphSubgraph {
  root: string
  root_type: string
  depth: number
  limit: number
  nodes: GraphNode[]
  edges: GraphEdge[]
  truncated: boolean
  truncated_by: string | null
}

export interface GraphStats {
  schema_version: string
  node_types: Record<string, number>
  edge_types: Record<string, number>
  node_total: number
  edge_total: number
  offset_relations: {
    considered: number
    used: number
    min_relevance: number
  }
}

export interface GraphPath {
  found: boolean
  hops: number
  path: string[]
  steps: { from: string; to: string; type: string }[]
  reason: string
}

export interface GraphRetrieveSnippet {
  evidence_id: string
  event_id: string
  document_id: string
  page: number | null
  section: string
  text: string
  confidence: number
  extraction_method: string
  is_simulated: boolean
}

export interface GraphRetrieveResult {
  provenance: string
  hops: number
  seed: string
  seed_rank: number
  path: string[]
  path_steps: { from: string; to: string; type: string; forward: boolean }[]
  path_labels: string[]
  snippet: GraphRetrieveSnippet
  event: {
    id: string
    event_type: string
    md: number
    tvd: number
    severity: string
    status: string
    description: string
    well_id: string
    document_id: string
  }
  well: {
    id: string
    name: string
    field: string
    operator: string
    status: string
  } | null
  formation: {
    code: string
    name: string
    top_depth: number
    bottom_depth: number
  } | null
}

export interface GraphRetrieve {
  query: string
  root: string
  max_hops: number
  provenance: string
  data_provenance: string
  parsed_intent: {
    event_type: string[]
    formations: string[]
    tvd_anchor_m: number | null
    md_anchor_m: number | null
    depth_window_m: number
    radius_km: number
    near_current_well: boolean
    intent: string
    keywords: string[]
    explain: string
  }
  seed_mode: string
  seeds: { node: string; event_id: string; rank: number; event_type: string; md: number; tvd: number }[]
  results: GraphRetrieveResult[]
  result_count: number
  snippets_examined: number
}

/* ------------------------------------------------------------------ *
 * Endpoints
 * ------------------------------------------------------------------ */

export const TelemetryApi = {
  channels: (wellId: string, signal?: AbortSignal) =>
    api.get<TelemetryChannels>(`/v1/telemetry/${encodeURIComponent(wellId)}/channels`, undefined, signal),

  snapshot: (wellId: string, signal?: AbortSignal) =>
    api.get<TelemetrySnapshot>(`/v1/telemetry/${encodeURIComponent(wellId)}/snapshot`, undefined, signal),

  stream: (
    wellId: string,
    params: { channels?: string[]; from_row?: number; to_row?: number; max_points?: number },
    signal?: AbortSignal,
  ) =>
    api.get<TelemetryStream>(`/v1/telemetry/${encodeURIComponent(wellId)}/stream`, params, signal),

  alerts: (
    wellId: string,
    params: {
      hazard?: string
      severity?: string
      detector?: string
      from_md?: number
      to_md?: number
      limit?: number
    } = {},
    signal?: AbortSignal,
  ) => api.get<TelemetryAlerts>(`/v1/telemetry/${encodeURIComponent(wellId)}/alerts`, params, signal),

  backtest: (wellId: string, params: { window?: number } = {}, signal?: AbortSignal) =>
    api.get<BacktestRun>(`/v1/telemetry/${encodeURIComponent(wellId)}/backtest`, params, signal),

  runBacktest: (wellId: string, params: { window?: number } = {}, signal?: AbortSignal) =>
    api.post<BacktestRun>(`/v1/telemetry/${encodeURIComponent(wellId)}/backtest/run`, undefined, params, signal),
}

export const AhpApi = {
  profiles: (signal?: AbortSignal) => api.get<AhpProfiles>('/v1/ahp/profiles', undefined, signal),
  profile: (hazard: string, signal?: AbortSignal) =>
    api.get<AhpProfile>(`/v1/ahp/profiles/${encodeURIComponent(hazard)}`, undefined, signal),
}

export const GraphApi = {
  stats: (signal?: AbortSignal) => api.get<GraphStats>('/v1/graph/stats', undefined, signal),
  subgraph: (
    params: { root: string; depth?: number; limit?: number },
    signal?: AbortSignal,
  ) => api.get<GraphSubgraph>('/v1/graph/subgraph', params, signal),
  path: (
    params: { from: string; to: string; max_hops?: number },
    signal?: AbortSignal,
  ) => api.get<GraphPath>('/v1/graph/path', params, signal),
  retrieve: (body: { query: string; root_id?: string; max_hops?: number }, signal?: AbortSignal) =>
    api.post<GraphRetrieve>('/v1/graph/retrieve', body, undefined, signal),
}
