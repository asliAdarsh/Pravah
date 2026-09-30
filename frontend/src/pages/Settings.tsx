import { useEffect, useState, type ReactNode } from 'react'
import type {
  MetaCounts,
  RelevanceConfigPost,
  RelevanceWeights,
  RiskConfig,
  RiskConfigPost,
} from '@/api/types'
import {
  Badge,
  Callout,
  ControlSlider,
  Disclosure,
  KeyStat,
  LineIcon,
  KeyValueGrid,
  Meter,
  ScreenHeader,
  SectionCard,
  SectionTitle,
  SegmentedControl,
  StatRow,
  actionButtonClass,
  controlClass,
  cx,
  ghostButtonClass,
} from '@/components/ui'
import { RELEVANCE_BANDS, fmtNumber, fmtPercent, humanizeEnum } from '@/lib/format'
import { THEME_OPTIONS, type ThemePreference } from '@/theme/useTheme'
import { PROTOTYPE_ROLES, type PrototypeRole, useApp } from '@/store/useApp'

/**
 * `/settings` — everything that used to be scattered around the working
 * screens: appearance, the prototype view switch, both rule engines, what the
 * dataset is, and how the whole thing works.
 *
 * Nothing here recomputes anything. Every value is read from the API, edited
 * through its `/config/*` endpoint, and rendered back from the response.
 */

/** The API returns two counts the shared response type does not declare. */
type FullCounts = MetaCounts & {
  evidence?: number
  offset_relations?: number
  telemetry_samples?: number
  anomaly_alerts?: number
}

type SeverityThresholds = { WARNING: number; HIGH: number; CRITICAL: number }

/** The risk POST body carries the thresholds, which the shared type omits. */
type RiskDraft = Omit<RiskConfigPost, 'severity_thresholds'> & {
  severity_thresholds: SeverityThresholds
}

const SEVERITY_ORDER: (keyof SeverityThresholds)[] = ['WARNING', 'HIGH', 'CRITICAL']

/** Prototype defaults, mirrored from the API so "Reset" needs no extra endpoint. */
const RELEVANCE_DEFAULTS: RelevanceConfigPost = {
  weights: {
    formation_similarity: 0.4,
    depth_similarity: 0.3,
    spatial_proximity: 0.2,
    event_similarity: 0.1,
  },
  radius_km: 8,
  min_relevance: 0.15,
}

const RISK_DEFAULTS: RiskDraft = {
  min_support_wells: 2,
  tvd_tolerance_m: 60,
  min_relevance: 0.25,
  formation_match_required: true,
  weights: {
    historical_event_match: 0.35,
    depth_proximity: 0.25,
    formation_similarity: 0.2,
    nearby_well_support: 0.15,
    operational_similarity: 0.05,
  },
  severity_thresholds: { WARNING: 0.45, HIGH: 0.6, CRITICAL: 0.78 },
}

const WEIGHT_LABELS: Record<keyof RelevanceWeights, string> = {
  formation_similarity: 'Formation similarity',
  depth_similarity: 'Depth similarity',
  spatial_proximity: 'Spatial proximity',
  event_similarity: 'Event similarity',
}

const RISK_WEIGHT_LABELS: Record<keyof RiskConfig['weights'], string> = {
  historical_event_match: 'Historical event match',
  depth_proximity: 'Depth proximity',
  formation_similarity: 'Formation similarity',
  nearby_well_support: 'Nearby well support',
  operational_similarity: 'Operational similarity',
}

const WEIGHT_KEYS = Object.keys(WEIGHT_LABELS) as (keyof RelevanceWeights)[]
const RISK_WEIGHT_KEYS = Object.keys(RISK_WEIGHT_LABELS) as (keyof RiskConfig['weights'])[]

export function Settings() {
  const {
    meta,
    relevanceConfig,
    riskConfig,
    role,
    setRole,
    theme,
    resolvedTheme,
    setTheme,
    applyRelevanceConfig,
    applyRiskConfig,
    recompute,
  } = useApp()

  const [relevanceDraft, setRelevanceDraft] = useState<RelevanceConfigPost>(RELEVANCE_DEFAULTS)
  const [riskDraft, setRiskDraft] = useState<RiskDraft>(RISK_DEFAULTS)
  const [saving, setSaving] = useState<'relevance' | 'risk' | 'reset' | null>(null)

  // The engine is the source of truth: whatever it last returned — including the
  // weights it re-normalised on write — becomes the form.
  useEffect(() => {
    if (relevanceConfig) {
      setRelevanceDraft({
        weights: relevanceConfig.weights,
        radius_km: relevanceConfig.radius_km,
        min_relevance: relevanceConfig.min_relevance,
      })
    }
  }, [relevanceConfig])

  useEffect(() => {
    if (riskConfig) {
      setRiskDraft({
        min_support_wells: riskConfig.min_support_wells,
        tvd_tolerance_m: riskConfig.tvd_tolerance_m,
        min_relevance: riskConfig.min_relevance,
        formation_match_required: riskConfig.formation_match_required,
        weights: riskConfig.weights,
        severity_thresholds: {
          WARNING: riskConfig.severity_thresholds.WARNING,
          HIGH: riskConfig.severity_thresholds.HIGH,
          CRITICAL: riskConfig.severity_thresholds.CRITICAL,
        },
      })
    }
  }, [riskConfig])

  const counts = meta?.counts as FullCounts | undefined
  const weightSum = Object.values(relevanceDraft.weights).reduce((sum, value) => sum + value, 0)
  const thresholds = riskDraft.severity_thresholds
  const thresholdsOrdered =
    thresholds.WARNING > 0 && thresholds.WARNING < thresholds.HIGH && thresholds.HIGH < thresholds.CRITICAL
  // The API rejects relevance weights that do not sum to 1.0 (±0.05); it does
  // not re-normalise a client's arithmetic, so the form has to hold the line.
  const weightsSumValid = Math.abs(weightSum - 1) <= 0.05

  async function saveRelevance(next: RelevanceConfigPost) {
    setSaving('relevance')
    try {
      await applyRelevanceConfig(next)
    } finally {
      setSaving(null)
    }
  }

  async function saveRisk(next: RiskDraft) {
    const ordered =
      next.severity_thresholds.WARNING > 0 &&
      next.severity_thresholds.WARNING < next.severity_thresholds.HIGH &&
      next.severity_thresholds.HIGH < next.severity_thresholds.CRITICAL
    if (!ordered) return
    setRiskDraft(next)
    setSaving('risk')
    try {
      await applyRiskConfig(next)
    } finally {
      setSaving(null)
    }
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-3 p-3">
      <ScreenHeader
        title="Settings"
        subtitle="Appearance, the view switch, and the two rule engines behind every score on the other screens."
        meta={
          <>
            <Badge tone="warning">{meta?.dataset_label ?? 'Real public data'}</Badge>
            <Badge tone="muted">engine {meta?.engine_version ?? '—'}</Badge>
          </>
        }
      />

      {/* ---------------------------------------------------- 1 · appearance */}
      <div id="settings-appearance">
        <SectionCard
          title={
            <span className="flex items-center gap-2">
              <span className="tnum text-fg-muted">1</span> Appearance
            </span>
          }
          description="Applies to the whole app. Nothing in this build is light-only."
        >
          <div className="flex flex-wrap items-start gap-4">
            <SegmentedControl<ThemePreference>
              label="Theme"
              value={theme}
              onChange={setTheme}
              options={THEME_OPTIONS}
            />
            <p className="text-2xs text-fg-muted">
              Currently:{' '}
              <span className="font-semibold text-fg-strong">{humanizeEnum(resolvedTheme)}</span>
              {theme === 'system' ? ' (following this device)' : ''}
            </p>
          </div>
        </SectionCard>
      </div>

      {/* ------------------------------------------------- 2 · prototype role */}
      <div id="settings-role">
        <SectionCard
          title={
            <span className="flex items-center gap-2">
              <span className="tnum text-fg-muted">2</span> View role
            </span>
          }
          description="Chooses the wording and framing of the screens, nothing else."
        >
          <div className="flex flex-wrap items-start gap-4">
            <label className="flex min-w-[200px] flex-col gap-1">
              <span className="text-2xs font-semibold uppercase tracking-[0.06em] text-fg-muted">
                View as
              </span>
              <select
                value={role}
                onChange={(event) => setRole(event.target.value as PrototypeRole)}
                aria-label="View role"
                className={cx(controlClass, 'h-8')}
              >
                {PROTOTYPE_ROLES.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.label}
                  </option>
                ))}
              </select>
            </label>
            <div className="min-w-[280px] flex-1">
              <Callout tone="info" title="A view switch, not authentication">
                The role is stored in this browser only. It grants no permission, hides no record and
                is not an identity — everyone using this build reads exactly the same published records.
              </Callout>
            </div>
          </div>
        </SectionCard>
      </div>

      {/* ---------------------------------------------- 3 · relevance engine */}
      <div id="settings-relevance">
        <SectionCard
          title={
            <span className="flex items-center gap-2">
              <span className="tnum text-fg-muted">3</span> Relevance engine
            </span>
          }
          description="Which offset wells count as relevant to the current well. Applying recomputes the offset relations and the alerts built on them."
          actions={
            <>
              <button
                type="button"
                className={ghostButtonClass}
                disabled={saving !== null}
                onClick={() => void saveRelevance(RELEVANCE_DEFAULTS)}
              >
                Reset to defaults
              </button>
              <button
                type="button"
                className={actionButtonClass}
                disabled={saving !== null || !weightsSumValid}
                onClick={() => void saveRelevance(relevanceDraft)}
              >
                {saving === 'relevance' ? 'Applying…' : 'Apply'}
              </button>
            </>
          }
        >
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="flex flex-col gap-2.5">
              {WEIGHT_KEYS.map((key) => (
                <ControlSlider
                  key={key}
                  label={WEIGHT_LABELS[key]}
                  value={relevanceDraft.weights[key]}
                  min={0}
                  max={1}
                  step={0.01}
                  format={(value) => fmtPercent(value, 0)}
                  onChange={(next) =>
                    setRelevanceDraft((draft) => ({
                      ...draft,
                      weights: { ...draft.weights, [key]: next },
                    }))
                  }
                />
              ))}
            </div>

            <div className="flex flex-col gap-2.5">
              <ControlSlider
                label="Search radius"
                value={relevanceDraft.radius_km}
                min={0.5}
                max={50}
                step={0.5}
                format={(value) => `${fmtNumber(value, 1)} km`}
                onChange={(next) => setRelevanceDraft((draft) => ({ ...draft, radius_km: next }))}
              />
              <ControlSlider
                label="Minimum relevance to list"
                value={relevanceDraft.min_relevance}
                min={0}
                max={1}
                step={0.01}
                format={(value) => fmtPercent(value, 0)}
                onChange={(next) => setRelevanceDraft((draft) => ({ ...draft, min_relevance: next }))}
              />
              <p className="text-2xs leading-relaxed text-fg-muted">
                The four weights must sum to 100% (±0.05). They currently sum to{' '}
                <span className={cx('tnum font-semibold', weightsSumValid ? 'text-fg-strong' : 'text-crit')}>
                  {fmtPercent(weightSum, 0)}
                </span>
                . The API rejects anything else rather than re-normalising it, so Apply stays disabled
                until they balance — the sliders then show exactly what the engine stored.
              </p>
            </div>
          </div>

          <div className="mt-3 border-t border-line pt-2.5">
            <h3 className="text-2xs font-semibold uppercase tracking-[0.08em] text-fg-strong">
              Bands applied to a relevance score
            </h3>
            <ul className="mt-1.5 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
              {RELEVANCE_BANDS.map((band) => (
                <li key={band.code} className="inset-surface px-2 py-1.5">
                  <p className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-[0.06em] text-fg-strong">
                    <span className={cx('size-1.5 shrink-0 rounded-full', band.fill)} />
                    {band.label}
                  </p>
                  <p className="tnum mt-0.5 text-2xs text-fg-muted">
                    {band.min === 0 ? 'below' : `≥ ${fmtPercent(band.min, 0)}`}
                  </p>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-2xs leading-relaxed text-fg-muted">
              {relevanceConfig?.weights_explanation ?? 'Engine defaults — not scientifically validated.'}{' '}
              These cut-offs belong to the engine; the API does not make them configurable.
            </p>
          </div>
        </SectionCard>
      </div>

      {/* --------------------------------------------------- 4 · risk engine */}
      <div id="settings-risk">
        <SectionCard
          title={
            <span className="flex items-center gap-2">
              <span className="tnum text-fg-muted">4</span> Risk engine
            </span>
          }
          description="The single rule that raises an alert, and the score bands it sorts alerts into. Applying recomputes alerts for every active well."
          actions={
            <>
              <button
                type="button"
                className={ghostButtonClass}
                disabled={saving !== null}
                onClick={() => void saveRisk(RISK_DEFAULTS)}
              >
                Reset to defaults
              </button>
              <button
                type="button"
                className={actionButtonClass}
                disabled={saving !== null || !thresholdsOrdered}
                onClick={() => void saveRisk(riskDraft)}
              >
                {saving === 'risk' ? 'Applying…' : 'Apply'}
              </button>
            </>
          }
        >
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="flex flex-col gap-2.5">
              <ControlSlider
                label="Minimum supporting wells"
                value={riskDraft.min_support_wells}
                min={1}
                max={20}
                step={1}
                format={(value) => `${fmtNumber(value)} ${value === 1 ? 'well' : 'wells'}`}
                onChange={(next) =>
                  setRiskDraft((draft) => ({ ...draft, min_support_wells: next }))
                }
              />
              <ControlSlider
                label="TVD tolerance"
                value={riskDraft.tvd_tolerance_m}
                min={5}
                max={600}
                step={5}
                format={(value) => `${fmtNumber(value)} m`}
                onChange={(next) => setRiskDraft((draft) => ({ ...draft, tvd_tolerance_m: next }))}
              />
              <ControlSlider
                label="Minimum relevance of a supporting well"
                value={riskDraft.min_relevance}
                min={0}
                max={1}
                step={0.01}
                format={(value) => fmtPercent(value, 0)}
                onChange={(next) => setRiskDraft((draft) => ({ ...draft, min_relevance: next }))}
              />
              <label className="flex items-center gap-2 text-xs text-fg">
                <input
                  type="checkbox"
                  checked={riskDraft.formation_match_required}
                  onChange={(event) =>
                    setRiskDraft((draft) => ({
                      ...draft,
                      formation_match_required: event.target.checked,
                    }))
                  }
                  className="size-3.5 accent-accent"
                />
                Formation match required (same or immediately adjacent)
              </label>
            </div>

            <div className="flex flex-col gap-2.5">
              <h3 className="text-2xs font-semibold uppercase tracking-[0.08em] text-fg-strong">
                Severity band thresholds
              </h3>
              {SEVERITY_ORDER.map((band) => (
                <ControlSlider
                  key={band}
                  label={`${humanizeEnum(band)} risk ≥`}
                  value={riskDraft.severity_thresholds[band]}
                  min={0.05}
                  max={0.99}
                  step={0.01}
                  format={(value) => fmtNumber(value, 2)}
                  onChange={(next) =>
                    setRiskDraft((draft) => ({
                      ...draft,
                      severity_thresholds: { ...draft.severity_thresholds, [band]: next },
                    }))
                  }
                />
              ))}
              {!thresholdsOrdered && (
                <p className="text-2xs text-crit">
                  Thresholds must satisfy 0 &lt; Warning &lt; High &lt; Critical. The API rejects
                  anything else, so Apply stays disabled until they are ordered.
                </p>
              )}
            </div>
          </div>

          <div className="mt-3 border-t border-line pt-2.5">
            <h3 className="text-2xs font-semibold uppercase tracking-[0.08em] text-fg-strong">
              Risk score weights in use
            </h3>
            <ul className="mt-1.5 grid gap-1.5 sm:grid-cols-2">
              {RISK_WEIGHT_KEYS.map((key) => (
                <li key={key} className="flex items-center gap-2">
                  <span className="w-44 shrink-0 truncate text-2xs text-fg-muted">
                    {RISK_WEIGHT_LABELS[key]}
                  </span>
                  <Meter value={riskDraft.weights[key]} fillClass="bg-accent" height={5} />
                  <span className="tnum w-10 shrink-0 text-right text-2xs text-fg-strong">
                    {fmtPercent(riskDraft.weights[key], 0)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </SectionCard>
      </div>

      {/* ------------------------------------------ 5 · dataset & provenance */}
      <div id="settings-dataset">
        <SectionCard
          title={
            <span className="flex items-center gap-2">
              <span className="tnum text-fg-muted">5</span> Dataset &amp; provenance
            </span>
          }
          description="What is loaded in this build, which published datasets it came from, and how to recompute it."
        >
          <StatRow>
            <KeyStat label="Wells" value={counts?.wells ?? '—'} />
            <KeyStat label="Formations" value={counts?.formations ?? '—'} />
            <KeyStat label="Events" value={counts?.events ?? '—'} />
            <KeyStat label="Documents" value={counts?.documents ?? '—'} />
            <KeyStat label="Evidence spans" value={counts?.evidence ?? '—'} />
            <KeyStat label="Alerts" value={counts?.alerts ?? '—'} />
            <KeyStat label="Offset relations" value={counts?.offset_relations ?? '—'} />
            <KeyStat
              label="Telemetry samples"
              value={counts?.telemetry_samples?.toLocaleString('en-US') ?? '—'}
              hint="real MWD/mud-logger samples"
            />
            <KeyStat label="Anomaly alerts" value={counts?.anomaly_alerts ?? '—'} />
            <KeyStat
              label="LLM mode"
              value={meta?.llm.mode ?? '—'}
              hint={meta?.llm.configured ? 'provider configured' : 'model: null — template synthesis'}
            />
          </StatRow>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              className={ghostButtonClass}
              disabled={saving !== null}
              onClick={() => {
                setSaving('reset')
                void recompute().finally(() => setSaving(null))
              }}
            >
              {saving === 'reset' ? 'Recomputing…' : 'Reset demo data'}
            </button>
            <p className="max-w-2xl text-2xs leading-relaxed text-fg-muted">
              Recomputes the derived layers in place — offset relations and alerts for the current
              well. The published source files under <code>backend/data_sources</code> are never
              modified.
            </p>
          </div>

          <div className="mt-3">
            <SectionTitle>Published sources</SectionTitle>
            <ul className="grid grid-cols-1 gap-1.5 md:grid-cols-2">
              {(meta?.data_sources ?? []).map((source) => (
                <li
                  key={source.id}
                  className="inset-surface flex items-start gap-2 px-2.5 py-2"
                >
                  <LineIcon name="database" size={13} className="mt-0.5 shrink-0 text-accent" />
                  <span className="min-w-0">
                    <span className="block text-xs font-medium text-fg-strong">{source.title}</span>
                    <span className="block text-2xs text-fg-muted">
                      {source.publisher} · {source.licence}
                      {source.size_bytes
                        ? ` · ${(source.size_bytes / 1048576).toFixed(1)} MB`
                        : ''}
                    </span>
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="block truncate text-2xs text-accent underline underline-offset-2"
                    >
                      {source.url}
                    </a>
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <div className="mt-3">
            <Disclosure
              summary="Configuration exactly as the API returns it"
              badge={<Badge tone="muted">read-only</Badge>}
            >
            <KeyValueGrid
              columns={2}
              items={[
                { label: 'App', value: meta?.app ?? '—', mono: true },
                { label: 'Version', value: meta?.version ?? '—', mono: true },
                { label: 'Engine version', value: meta?.engine_version ?? '—', mono: true },
                { label: 'Data provenance', value: meta?.data_provenance ?? '—', mono: true },
                { label: 'Demo scenario', value: meta?.demo.scenario ?? '—', mono: true },
                { label: 'Relevance config version', value: relevanceConfig?.version ?? '—', mono: true },
                { label: 'Relevance radius', value: fmtNumber(relevanceConfig?.radius_km, 1) + ' km', mono: true },
                {
                  label: 'Severity thresholds',
                  value: SEVERITY_ORDER.map(
                    (band) => `${humanizeEnum(band)} ${fmtNumber(thresholds[band], 2)}`,
                  ).join(' · '),
                  mono: true,
                },
              ]}
            />
            </Disclosure>
          </div>
        </SectionCard>
      </div>

      {/* ----------------------------------------------- 6 · how it works */}
      <div id="settings-method">
        <SectionCard
          title={
            <span className="flex items-center gap-2">
              <span className="tnum text-fg-muted">6</span> How this works
            </span>
          }
          description="The method notes that used to sit loose on the working screens. They are reference material, so they stay closed until asked for."
        >
          <div className="flex flex-col gap-2">
            <Disclosure summary="How relevance is weighted">
              <Method>
                <p>
                  Each offset well is scored as a weighted sum of four similarities, all computed by the
                  backend and returned with the score — the UI never recomputes them:
                </p>
                <ul className="mt-1 list-disc pl-4">
                  <li>
                    <strong>Formation similarity</strong> — how close the offset well&apos;s depth range
                    sits to the current one.
                  </li>
                  <li>
                    <strong>Depth similarity</strong> — overlap between the two depth ranges.
                  </li>
                  <li>
                    <strong>Spatial proximity</strong> — great-circle distance, computed in Python rather
                    than in the database.
                  </li>
                  <li>
                    <strong>Event similarity</strong> — overlap in the event types each well has logged.
                  </li>
                </ul>
                <p className="mt-1">
                  Every score ships with a <code>factors[]</code> array — code, label, value, weight,
                  contribution and a plain-English detail — and that array is the only thing the
                  &ldquo;why relevant&rdquo; panels render.
                </p>
              </Method>
            </Disclosure>

            <Disclosure summary="How the alert rule fires">
              <Method>
                <p>
                  One rule, run per well: take every relevant offset well, collect the hazard events
                  inside the current position&apos;s TVD window widened by{' '}
                  <strong>{fmtNumber(riskDraft.tvd_tolerance_m)} m</strong>, and keep only the wells
                  whose relevance is at least <strong>{fmtPercent(riskDraft.min_relevance, 0)}</strong>.
                  The rule fires when at least <strong>{fmtNumber(riskDraft.min_support_wells)}</strong>{' '}
                  such wells support the same event type
                  {riskDraft.formation_match_required
                    ? ', and those events sit in the same formation or an immediately adjacent one'
                    : ''}
                  . The risk score is the weighted sum of the five risk factors, and the band it falls
                  into comes from the thresholds in section 4.
                </p>
              </Method>
            </Disclosure>

            <Disclosure summary="What is derived, and what is not measured">
              <Method>
                <ul className="list-disc pl-4">
                  <li>
                    Wells, coordinates, formation tops, casing, the Daily Drilling Reports, the
                    events extracted from them and every telemetry sample come from the published
                    files listed above. Nothing in the record is generated.
                  </li>
                  <li>
                    The live wellbore <code>15/9-F-9A</code> has no published coordinate or
                    stratigraphy row, so its position and formation column are derived from the
                    five wells in the same <code>15/9</code> block and labelled as such wherever
                    they appear.
                  </li>
                  <li>
                    The Directorate publishes formation tops, not directional surveys, so a
                    trajectory here is the vertical line through the published depths. That is a
                    statement about what is known, not a modelled deviation.
                  </li>
                  <li>
                    The OCR stage is skipped when no engine is installed rather than faked.
                    Page numbers and bounding boxes come from the source or are <code>null</code>,
                    and the UI prints &ldquo;Page not available in this record&rdquo; instead of
                    guessing one.
                  </li>
                </ul>
              </Method>
            </Disclosure>

            <Disclosure summary="What the LLM may and may not do">
              <Method>
                <p>
                  With no provider key configured, every synthesis block is{' '}
                  <code>RULE_BASED_TEMPLATE</code> with <code>model: null</code> — deterministic text
                  assembled from stored records. A provider error is caught and downgraded to the same
                  template.
                </p>
                <p className="mt-1">
                  <code>MODEL_GENERATED</code> appears only when a call actually succeeded, and the model
                  name is the provider&apos;s own echo — never invented. The model may rephrase the
                  answer and the citation list; it may not invent an event, a page number, a well or a
                  score. Structured records stay the primary content, and every generated sentence
                  carries its provenance label.
                </p>
              </Method>
            </Disclosure>

            <Disclosure summary="Where every number comes from">
              <Method>
                <p>
                  Explainability on every screen is rendered from the engine&apos;s own{' '}
                  <code>factors[]</code>. Relevance and risk are labelled{' '}
                  <code>HEURISTIC</code>: the weights are engineering priors chosen to make the
                  reasoning legible, not a fit against outcomes. They are shown in full, with their
                  consistency ratio, on the Relevance Method screen.
                </p>
                <p className="mt-1">
                  The one figure measured against an outcome is the telemetry lead time on the
                  Backtest screen, and it is reported with its Wilson 95% interval, its precision
                  definition and a leakage audit — not as a single confident number. Red is
                  reserved for critical.
                </p>
              </Method>
            </Disclosure>
          </div>
        </SectionCard>
      </div>
    </div>
  )
}

function Method({ children }: { children: ReactNode }) {
  return <div className="max-w-3xl space-y-1.5 text-xs leading-relaxed text-fg-muted">{children}</div>
}
