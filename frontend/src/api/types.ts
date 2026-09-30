/**
 * Typed models for every payload in `docs/API_CONTRACT.md`.
 *
 * Field names mirror the contract exactly (snake_case). Optionality follows the
 * contract: where the API can legitimately return `null` (page numbers, bounding
 * boxes, LLM model, unattached documents) the field is typed `| null` and the UI
 * must render an explicit "not available" affordance rather than inventing a value.
 */

/* ------------------------------------------------------------------ *
 * Enums (contract §2)
 * ------------------------------------------------------------------ */

export type WellStatus = 'PLANNED' | 'DRILLING' | 'SUSPENDED' | 'COMPLETED' | 'ABANDONED'

export type DocumentType =
  | 'DDR'
  | 'WCR'
  | 'DGR'
  | 'INCIDENT_REPORT'
  | 'WELL_LOG'
  | 'LESSONS_LEARNED'

export type EventType =
  | 'MUD_LOSS'
  | 'STUCK_PIPE'
  | 'KICK'
  | 'TORQUE_SPIKE'
  | 'OVERPRESSURE'
  | 'LOST_CIRCULATION'
  | 'CEMENTING_ISSUE'
  | 'DRILLING_DYSFUNCTION'
  | 'NPT'
  | 'FORMATION_TRANSITION'
  | 'HOLE_INSTABILITY'
  | 'OTHER'

export type EventSeverity = 'LOW' | 'MODERATE' | 'HIGH' | 'CRITICAL'

export type AlertStatus = 'OPEN' | 'ACKNOWLEDGED' | 'DISMISSED' | 'CLOSED'

export type SeverityBand = 'INFO' | 'WARNING' | 'HIGH' | 'CRITICAL'

export type ActionType = 'ACKNOWLEDGE' | 'NOTE' | 'STATUS_CHANGE' | 'REVIEW'

export type TimelineEntryKind =
  | 'drilling_event'
  | 'formation_transition'
  | 'current_marker'
  | 'mitigation'

export type DataProvenance = 'REAL_PUBLIC_DATA' | 'OPERATOR_SUPPLIED_UNVERIFIED'

/** Mandatory on every generated sentence (contract §3, `provenance`). */
export type TextProvenance =
  | 'RULE_BASED_TEMPLATE'
  | 'MODEL_GENERATED'
  | 'SOURCE_DOCUMENT'
  | 'SYSTEM_RULE'

export type ScoreMethod = 'PROTOTYPE_HEURISTIC'

/* ------------------------------------------------------------------ *
 * Shared building blocks
 * ------------------------------------------------------------------ */

export interface MetaCounts {
  wells: number
  formations: number
  events: number
  documents: number
  alerts: number
}

export interface EventTypeRegistryEntry {
  code: EventType
  label: string
  family: string
  severity_weight: number
  color: string
}

export interface RelevanceWeights {
  formation_similarity: number
  depth_similarity: number
  spatial_proximity: number
  event_similarity: number
}

export interface RiskWeights {
  historical_event_match: number
  depth_proximity: number
  formation_similarity: number
  nearby_well_support: number
  operational_similarity: number
}

export interface RelevanceConfig {
  weights: RelevanceWeights
  radius_km: number
  min_relevance: number
  weights_explanation: string
  version: string
}

export interface RiskConfig {
  min_support_wells: number
  tvd_tolerance_m: number
  min_relevance: number
  formation_match_required: boolean
  weights: RiskWeights
  severity_thresholds: Record<SeverityBand, number>
  version?: string
}

export interface LlmConfig {
  configured: boolean
  mode: string
}

export interface DemoInfo {
  current_well_id: string
  scenario: string
}

export interface HealthResponse {
  status: string
  version: string
  engine_version: string
  database: string
  data_sources: DataSourceInfo[]
  telemetry_samples?: number
  anomaly_alerts?: number
}

export interface DataSourceInfo {
  id: string
  title: string
  publisher: string
  url: string
  licence: string
  present: boolean
  size_bytes?: number
}

export interface DataSourceInfo {
  id: string
  title: string
  publisher: string
  url: string
  licence: string
  present: boolean
  size_bytes?: number
}

export interface MetaResponse {
  app: string
  version: string
  dataset_label: string
  data_provenance: DataProvenance
  engine_version: string
  data_sources: DataSourceInfo[]
  counts: MetaCounts
  event_types: EventTypeRegistryEntry[]
  severity_bands: SeverityBand[]
  relevance_config: RelevanceConfig
  risk_config: RiskConfig
  llm: LlmConfig
  demo: DemoInfo
}

export interface Formation {
  id: string | number
  name: string
  code: string
  top_depth: number
  bottom_depth: number
  lithology: string
  depositional_environment: string
  age: string
  description: string
  color: string
  is_simulated: boolean
}

export interface FormationsResponse {
  items: Formation[]
}

export interface CurrentFormationRef {
  id: string | number
  name: string
  top_depth?: number
  bottom_depth?: number
  lithology?: string
}

export interface CurrentFormationDetail extends CurrentFormationRef {
  top_depth: number
  bottom_depth: number
  lithology: string
}

export interface WellSummary {
  id: string
  name: string
  field: string
  block: string
  latitude: number
  longitude: number
  status: WellStatus
  current_depth_md: number
  current_tvd: number
  current_formation: CurrentFormationRef
  operator: string
  well_type: string
  spud_date: string
  water_depth_m: number
  is_active: boolean
  offset_well_count: number
  relevant_event_count: number
  top_alert_severity: SeverityBand | null
  data_provenance: DataProvenance
}

export interface TrajectoryPoint {
  md: number
  tvd: number
  inclination: number
}

export interface OperatingContext {
  section_size: string
  bit_size: string
  mud_weight_ppg: number
  rop_mph: number
  wob_klb: number
  block: string
  status_note: string
}

export interface Well extends WellSummary {
  completion_date: string | null
  rig: string
  mud_system: string
  total_depth_md: number
  current_formation: CurrentFormationDetail
  trajectory: TrajectoryPoint[]
  operating_context: OperatingContext
}

export interface WellsResponse {
  items: WellSummary[]
  total: number
}

export interface SimilarityBreakdown {
  formation_similarity: number
  depth_similarity: number
  spatial_proximity: number
  event_similarity: number
}

export interface DepthRange {
  min_md: number
  max_md: number
  max_tvd: number
}

export interface SourceAvailability {
  documents: number
  with_evidence: number
  coverage: 'NONE' | 'PARTIAL' | 'FULL' | (string & {})
}

export interface DocumentRef {
  id: string
  doc_type: DocumentType
  doc_type_label: string
  title: string
  filename: string
  doc_date: string
  page_count: number
  source_system: string
}

export interface EventBrief {
  id: string
  well_id: string
  well_name: string
  event_type: EventType
  event_label: string
  md: number
  tvd: number
  formation: string
  severity: EventSeverity
  severity_score: number
  occurred_at: string
  description: string
  mitigation: string
}

export interface EventDetail extends EventBrief {
  event_subtype: string
  status: string
  days_open: number
  document: DocumentRef | null
  evidence_count: number
  data_provenance: DataProvenance
}

export interface Factor {
  code: string
  label: string
  value: number
  weight: number
  contribution: number
  detail: string
}

export interface OffsetWell {
  well: WellSummary
  distance_km: number
  relevance_score: number
  relevance_band: SeverityBand
  similarity: SimilarityBreakdown
  factors: Factor[]
  why_relevant: string[]
  depth_range: DepthRange
  relevant_events: EventBrief[]
  event_count: number
  document_count: number
  source_availability: SourceAvailability
  selected: boolean
}

export interface NearbyResponse {
  current_well: WellSummary
  radius_km: number
  min_relevance: number
  weights: RelevanceWeights
  method: ScoreMethod
  items: OffsetWell[]
  data_provenance: DataProvenance
}

export interface TimelineEntry {
  id: string
  kind: TimelineEntryKind
  origin: 'CURRENT' | 'OFFSET' | (string & {})
  well_id: string
  well_name: string
  event_type: EventType | null
  event_label: string | null
  md: number
  tvd: number
  formation: string | null
  severity: EventSeverity | null
  severity_score: number | null
  occurred_at: string | null
  description: string | null
  mitigation: string | null
  delta_from_current_md: number | null
  relevance_note: string | null
  document_id: string | null
  evidence_count: number
}

export interface TimelineWindow {
  top_md: number
  bottom_md: number
  tvd_at_current: number
}

export interface CurrentMarker {
  md: number
  tvd: number
  formation: string
  label: string
}

export interface TimelineResponse {
  current_well: WellSummary
  window: TimelineWindow
  current_marker: CurrentMarker
  entries: TimelineEntry[]
  method: ScoreMethod
  data_provenance: DataProvenance
}

export interface EventsResponse {
  items: EventDetail[]
  total: number
}

export interface WellEventsResponse {
  items: EventDetail[]
}

export interface EvidenceBrief {
  id: string
  page: number | null
  section: string
  text_span: string
  confidence: number
  bbox: number[] | null
  extraction_method: string
}

export interface EvidenceContext {
  previous_event: EventBrief | null
  next_event: EventBrief | null
  document_excerpt: string
}

export interface EvidenceReason {
  alert_id: string
  reason: string
  factors: Factor[]
}

export interface EvidenceChain {
  alert_ids: string[]
  reasons: EvidenceReason[]
  well: WellSummary
  document: DocumentRef | null
  evidence: EvidenceBrief[]
}

export interface EvidenceResponse {
  event: EventDetail
  chain: EvidenceChain
  document: DocumentRef | null
  context: EvidenceContext
  evidence: EvidenceBrief[]
}

/* ----------------------------- Documents ----------------------------- */

export interface DocumentSummary {
  id: string
  well_id: string | null
  well_name: string | null
  doc_type: DocumentType
  doc_type_label: string
  title: string
  filename: string
  doc_date: string
  source_system: string
  page_count: number
  event_count: number
  evidence_count: number
  ocr_engine: string
  extraction_method: string
  is_simulated: boolean
  data_provenance: DataProvenance
}

export interface DocumentsResponse {
  items: DocumentSummary[]
}

export interface DocumentSection {
  heading: string
  page: number | null
  text: string
}

export interface DocumentDetail extends DocumentSummary {
  excerpt: string
  sections: DocumentSection[]
}

export interface PipelineStage {
  stage: string
  status: string
  detail: string
  simulated: boolean
}

export interface IngestResponse {
  document: DocumentSummary
  pipeline: PipelineStage[]
  extracted_events: EventDetail[]
  sections: DocumentSection[]
  warnings: string[]
  data_provenance: DataProvenance
}

export interface IngestRequest {
  filename: string
  well_id: string | null
  doc_type: DocumentType
  doc_date: string
  text: string
  ocr_engine: string
}

/* ------------------------------ Replay ------------------------------- */

export interface FormationColumnRow {
  name: string
  top_depth: number
  bottom_depth: number
  current: boolean
}

export interface ReplayOffsetWell {
  well: WellSummary
  distance_km: number
  relevance_score: number
  relevance_band: SeverityBand
  similarity: SimilarityBreakdown
  why_relevant: string[]
  factors: Factor[]
  events: EventDetail[]
  formation_alignment: string
}

export interface RecurringHazard {
  event_type: EventType
  event_label: string
  well_count: number
  event_count: number
  tvd_interval: { top: number; bottom: number }
  depth_below_current_m: number | null
  wells: string[]
}

export interface ReplayWindow {
  top_tvd: number
  bottom_tvd: number
}

export interface ReplayPayload {
  current_well: Well
  window: ReplayWindow
  formation_column: FormationColumnRow[]
  offset_wells: ReplayOffsetWell[]
  current_events: EventDetail[]
  recurring_hazards: RecurringHazard[]
  alerts: AlertBrief[]
  method: ScoreMethod
  data_provenance: DataProvenance
}

/* ------------------------------ Alerts ------------------------------- */

export interface AlertInterval {
  top_tvd: number
  bottom_tvd: number
}

export interface AlertBrief {
  id: string
  current_well_id: string
  current_well_name: string
  event_type: EventType
  event_label: string
  title: string
  severity_band: SeverityBand
  risk_score: number
  current_tvd: number
  interval: AlertInterval
  formation: string
  formation_match: string
  supporting_well_count: number
  supporting_event_count: number
  distance_to_interval_m: number
  status: AlertStatus
  created_at: string
  updated_at: string
  is_active: boolean
  headline: string
  rule_id: string
  rule_version: string
  data_provenance: DataProvenance
}

export interface AlertsResponse {
  items: AlertBrief[]
  total: number
  counts: Partial<Record<AlertStatus, number>>
  limit?: number
  offset?: number
  data_provenance: DataProvenance
}

export interface RuleInfo {
  rule_id: string
  version: string
  method: ScoreMethod
  config: {
    min_support_wells: number
    tvd_tolerance_m: number
    min_relevance: number
    formation_match_required: boolean
    weights: RiskWeights
  }
}

/** q1 — the engine's own structured statement, with the events it counted. */
export interface DecisionEventRef {
  event_id: string
  well_id: string
  event_type: EventType
  md: number
  tvd: number
  severity: EventSeverity
  document_id: string | null
}

export interface DecisionQuestion1 {
  text: string
  event_type: EventType
  event_label: string
  interval: AlertInterval
  supporting_event_count: number
  supporting_well_count: number
  severity_band: SeverityBand
  risk_score: number
  events: DecisionEventRef[]
  provenance: TextProvenance
}

/** q3 — the contract's "OffsetWell-lite": flat, no nested well record. */
export interface DecisionSupportingWell {
  well_id: string
  distance_km: number
  relevance_score: number
  relevance_band: SeverityBand
  why_relevant: string[]
  event_count: number
}

/** q4 — the contract's "EvidenceBrief + document + well + alert_id", flattened. */
export interface DecisionEvidenceItem {
  evidence_id: string
  event_id: string
  well_id: string
  well_name: string
  page: number | null
  section: string
  text_span: string
  confidence: number
  extraction_method: string
  document_id: string | null
  document_ref: string | null
  alert_id: string
}

export interface ProvenanceText {
  text: string
  provenance: TextProvenance
  /** Present on engine-generated bullets (q2, q6); absent on quoted document text (q5). */
  code?: string
  source_event_id?: string | null
  source_document?: string | null
}

export interface GeneratedSummary {
  text: string
  provenance: TextProvenance
  model: string | null
  citations: SearchCitation[]
  disclaimer: string
}

export interface EvidenceChainStep {
  step: 'ALERT' | 'REASON' | 'EVENT' | 'WELL' | 'DOCUMENT' | 'EVIDENCE' | (string & {})
  ref: string
  detail: string
}

export interface EngineerAction {
  id: string
  alert_id: string
  action_type: ActionType
  engineer: string
  note: string
  created_at: string
  from_status?: AlertStatus | null
  to_status?: AlertStatus | null
}

export interface RiskBreakdown {
  historical_event_match: number
  depth_proximity: number
  formation_similarity: number
  nearby_well_support: number
  operational_similarity: number
  total?: number
}

export interface AlertDecision {
  q1_what_happened: DecisionQuestion1
  q2_why_relevant_now: ProvenanceText[]
  q3_supporting_wells: DecisionSupportingWell[]
  q4_evidence: DecisionEvidenceItem[]
  q5_historical_mitigation: ProvenanceText[]
  q6_what_to_review: ProvenanceText[]
}

export interface AlertDetail {
  alert: AlertBrief
  rule: RuleInfo
  decision: AlertDecision
  risk_factors: Factor[]
  risk_breakdown: RiskBreakdown
  supporting_events: EventDetail[]
  evidence_chain: EvidenceChainStep[]
  actions: EngineerAction[]
  generated_summary: GeneratedSummary
}

export interface AlertStatusResponse {
  alert: AlertBrief
  actions: EngineerAction[]
  data_provenance: DataProvenance
}

/** `POST /alerts/{id}/notes` returns the action with the refreshed alert attached. */
export type NoteResponse = EngineerAction & {
  alert: AlertBrief
  data_provenance: DataProvenance
}

export interface AlertStatusRequest {
  engineer: string
  status: AlertStatus
  note: string
}

export interface NoteRequest {
  engineer: string
  note: string
}

/* ------------------------------ Search ------------------------------- */

export interface SearchFilters {
  radius_km: number
  event_type: EventType | null
  formation: string | null
  tvd_min: number | null
  tvd_max: number | null
}

export interface SearchRequest {
  query: string
  current_well_id: string | null
  filters: SearchFilters
  limit: number
}

export interface ParsedIntent {
  event_type: EventType[]
  formations: string[]
  tvd_anchor_m: number | null
  md_anchor_m: number | null
  depth_window_m: number | null
  radius_km: number | null
  near_current_well: boolean
  intent: string
  keywords: string[]
  explain: string
}

export interface MitigationRecord {
  text: string
  provenance: TextProvenance
  event_id: string
  well_name: string
  document_ref: string | null
}

export interface SearchCitation {
  event_id: string | null
  document_id: string | null
  page: number | null
  label: string
}

export interface SearchSynthesis {
  text: string
  provenance: TextProvenance
  model: string | null
  citations: SearchCitation[]
  disclaimer: string
}

export interface SearchStructuredResults {
  events: EventDetail[]
  wells: OffsetWell[]
  documents: DocumentSummary[]
  mitigations: MitigationRecord[]
  evidence: EvidenceBrief[]
  alerts: AlertBrief[]
}

export interface SearchResponse {
  query: string
  current_well_id: string | null
  parsed_intent: ParsedIntent
  structured_results: SearchStructuredResults
  synthesis: SearchSynthesis
  result_count: number
  retrieval: Record<string, unknown>
  data_provenance: DataProvenance
}

/* ------------------------------ Config ------------------------------- */

export interface RelevanceConfigResponse {
  weights: RelevanceWeights
  radius_km: number
  min_relevance: number
  weights_explanation: string
  version: string
}

export interface RelevanceConfigPost {
  weights: RelevanceWeights
  radius_km: number
  min_relevance: number
}

export interface RiskConfigPost {
  min_support_wells: number
  tvd_tolerance_m: number
  min_relevance: number
  formation_match_required: boolean
  weights: RiskWeights
}

export interface RecomputeResponse {
  offset_relations_updated: number
  alerts_created: number
  alerts_updated: number
  alerts: AlertBrief[]
}

export interface DemoScenarioResponse {
  scenario: Record<string, unknown>
  current_well: Well
  nearby_wells: OffsetWell[]
  replay: ReplayPayload
  alerts: AlertBrief[]
  documents: DocumentSummary[]
  event_types: EventTypeRegistryEntry[]
}
