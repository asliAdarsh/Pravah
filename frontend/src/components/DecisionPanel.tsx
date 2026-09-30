import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { Api, ApiError } from '@/api/client'
import type { AlertBrief, AlertDetail } from '@/api/types'
import { useAsyncData } from '@/lib/useAsyncData'
import {
  bandTone,
  fmtDateTime,
  fmtKm,
  fmtMeters,
  fmtNumber,
  fmtPage,
  fmtPercent,
  humanizeEnum,
  relevanceTone,
} from '@/lib/format'
import { notifyRecordsChanged } from '@/lib/useAsyncData'
import { useApp } from '@/store/useApp'
import { Badge, EmptyState, Meter, Skeleton, cx, uniqueReasons } from './primitives'
import { Disclaimer, GeneratedList, ProvenanceTag } from './Provenance'
import { WhyFactors } from './WhyFactors'
import { LineIcon } from './LineIcon'

const RISK_LABELS: Record<string, string> = {
  historical_event_match: 'Historical event match',
  depth_proximity: 'Depth proximity',
  formation_similarity: 'Formation similarity',
  nearby_well_support: 'Nearby well support',
  operational_similarity: 'Operational similarity',
}

const CHAIN_STEP_ORDER = ['ALERT', 'REASON', 'EVENT', 'WELL', 'DOCUMENT', 'EVIDENCE'] as const

/**
 * Engineer decision panel. Mounted globally by the AppShell; any screen calls
 * `useApp().openDecision(alertId)`. Reads `GET /alerts/{id}` (the richest
 * contract endpoint) and posts engineer actions to the acknowledge / notes
 * endpoints, refreshing the alert optimistically.
 */
export function DecisionPanel({ alertId, onClose }: { alertId: string; onClose?: () => void }) {
  const { closeDecision, openEvidence, notify, role } = useApp()
  const navigate = useNavigate()

  const [engineer, setEngineer] = useState('A. Sharma')
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState<'acknowledge' | 'note' | null>(null)
  const [optimisticAlert, setOptimisticAlert] = useState<AlertBrief | null>(null)

  const { data, error, loading, reload } = useAsyncData<AlertDetail>(
    (signal) => Api.alert(alertId, signal),
    [alertId],
  )

  useEffect(() => {
    setOptimisticAlert(null)
    setNote('')
  }, [alertId])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') (onClose ?? closeDecision)()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, closeDecision])

  const alert = optimisticAlert ?? data?.alert ?? null
  const decision = data?.decision

  async function runAction(kind: 'acknowledge' | 'note') {
    if (submitting) return
    if (kind === 'note' && note.trim().length === 0) {
      notify('Add note text before posting an engineer note', 'warning')
      return
    }
    setSubmitting(kind)
    try {
      if (kind === 'acknowledge') {
        const response = await Api.acknowledge(alertId, { engineer, note: note.trim() })
        setOptimisticAlert(response.alert)
        notify(`${alertId} acknowledged · ${response.actions.length} action(s) recorded`, 'success')
      } else {
        await Api.addNote(alertId, { engineer, note: note.trim() })
        notify('Engineer note recorded on the alert', 'success')
        setNote('')
      }
      reload()
      notifyRecordsChanged()
    } catch (cause) {
      notify(cause instanceof ApiError ? cause.message : 'Could not post the engineer action', 'critical')
    } finally {
      setSubmitting(null)
    }
  }

  const firstLinkedEventId =
    decision?.q1_what_happened.events[0]?.event_id ?? data?.supporting_events[0]?.id ?? null

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Engineer decision panel">
      <button
        type="button"
        aria-label="Close decision panel"
        onClick={onClose ?? closeDecision}
        className="absolute inset-0 bg-chrome/40"
      />

      <aside className="relative flex h-full w-full max-w-[640px] flex-col border-l border-line-strong bg-surface-1 shadow-[var(--shadow-raised)]">
        <header className="flex items-start justify-between gap-3 border-b border-line bg-chrome px-3 py-2 text-chrome-fg">
          <div className="min-w-0">
            <p className="text-2xs font-semibold uppercase tracking-[0.1em] text-chrome-muted">
              Engineer decision panel
            </p>
            <h2 className="truncate text-sm font-semibold">{alert ? alert.title : alertId}</h2>
            {alert && (
              <p className="truncate text-2xs text-chrome-muted">
                {alert.id} · {alert.current_well_name} · {humanizeEnum(alert.event_type)} · risk{' '}
                {fmtNumber(alert.risk_score, 2)}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose ?? closeDecision}
            className="shrink-0 rounded-sm border border-chrome-2 p-1 text-chrome-fg hover:bg-chrome-2"
            aria-label="Close"
          >
            <LineIcon name="close" size={14} />
          </button>
        </header>

        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
          {loading && !data && (
            <div className="space-y-2 p-3">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          )}

          {error && (
            <div className="m-3 rounded-sm border border-crit-line bg-crit-soft p-2">
              <p className="text-xs font-semibold text-crit">{error}</p>
              <button
                type="button"
                onClick={reload}
                className="mt-1.5 inline-flex items-center gap-1 text-2xs font-semibold text-crit underline"
              >
                <LineIcon name="refresh" size={12} /> RETRY
              </button>
            </div>
          )}

          {data && alert && decision && (
            <div className="divide-y divide-line">
              {/* Alert header */}
              <section className="px-3 py-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge tone={bandTone(alert.severity_band)}>{humanizeEnum(alert.severity_band)}</Badge>
                  <Badge tone="muted">{humanizeEnum(alert.status)}</Badge>
                  <Badge tone="muted">rule {data.rule.rule_id}</Badge>
                  <Badge tone="info">{data.rule.method}</Badge>
                </div>
                <p className="mt-1.5 text-sm leading-snug text-fg">{alert.headline}</p>
                <p className="mt-1 text-2xs text-fg-muted">
                  created {fmtDateTime(alert.created_at)} · {alert.supporting_well_count} supporting well(s) ·{' '}
                  {alert.supporting_event_count} supporting event(s) · {humanizeEnum(alert.formation_match)} formation
                  match at {fmtMeters(alert.current_tvd)} TVD
                </p>
              </section>

              {/* Generated summary — provenance mandatory */}
              <section className="px-3 py-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-2xs font-semibold uppercase tracking-[0.08em] text-fg-strong">
                    Engine summary
                  </span>
                  <ProvenanceTag provenance={data.generated_summary.provenance} model={data.generated_summary.model} />
                </div>
                <p className="mt-1 text-sm leading-snug text-fg">{data.generated_summary.text}</p>
                {data.generated_summary.citations.length > 0 && (
                  <ul className="mt-1.5 space-y-0.5">
                    {data.generated_summary.citations.map((citation, index) => (
                      <li key={`${citation.event_id ?? 'src'}-${index}`} className="text-2xs text-fg-muted">
                        <span className="font-semibold uppercase tracking-[0.06em] text-fg-muted">Source</span>{' '}
                        <span className="tnum">{citation.event_id ?? '—'}</span>
                        {citation.document_id && (
                          <>
                            {' · '}
                            <span className="tnum">{citation.document_id}</span>
                          </>
                        )}
                        {' · '}
                        <span className={cx(citation.page === null && 'text-warn')}>
                          {fmtPage(citation.page)}
                        </span>
                        {citation.label && <span className="text-fg-muted"> — {citation.label}</span>}
                      </li>
                    ))}
                  </ul>
                )}
                <Disclaimer className="mt-1.5">{data.generated_summary.disclaimer}</Disclaimer>
              </section>

              {/* Risk breakdown */}
              <section className="px-3 py-2">
                <h3 className="text-2xs font-semibold uppercase tracking-[0.08em] text-fg-strong">
                  Risk breakdown · engine risk score {fmtNumber(alert.risk_score, 2)}
                </h3>
                <ul className="mt-1.5 space-y-1">
                  {CHAIN_LABELS.map((key) => (
                    <li key={key} className="grid grid-cols-[150px_1fr_46px] items-center gap-2">
                      <span className="truncate text-xs text-fg">{RISK_LABELS[key]}</span>
                      <Meter
                        value={data.risk_breakdown[key]}
                        // Factor strength is shown by the number and the bar
                        // length; red stays reserved for the alert band itself,
                        // so five high factors must not paint the panel critical.
                        fillClass="bg-accent"
                        height={5}
                        label={RISK_LABELS[key]}
                      />
                      <span className="tnum text-right text-2xs text-fg-muted">
                        {fmtNumber(data.risk_breakdown[key], 2)}
                      </span>
                    </li>
                  ))}
                </ul>
                <div className="mt-2 border-t border-line pt-1.5">
                  <WhyFactors factors={data.risk_factors} title="Why this alert" />
                </div>
              </section>

              {/* Q1 */}
              <Question index="Q1" title="What happened">
                <div className="flex flex-wrap items-center gap-1.5">
                  <ProvenanceTag provenance={decision.q1_what_happened.provenance} />
                  <Badge tone={bandTone(decision.q1_what_happened.severity_band)}>
                    {humanizeEnum(decision.q1_what_happened.severity_band)}
                  </Badge>
                  <Badge tone="muted">risk {fmtNumber(decision.q1_what_happened.risk_score, 2)}</Badge>
                </div>
                <p className="mt-1 text-sm leading-snug text-fg">{decision.q1_what_happened.text}</p>
                <dl className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
                  <Field label="Hazard">{decision.q1_what_happened.event_label}</Field>
                  <Field label="Current TVD">{fmtMeters(alert.current_tvd)}</Field>
                  <Field label="Hazard interval">
                    {fmtMeters(decision.q1_what_happened.interval.top_tvd)} –{' '}
                    {fmtMeters(decision.q1_what_happened.interval.bottom_tvd)} TVD
                  </Field>
                  <Field label="Formation">
                    {alert.formation} ({humanizeEnum(alert.formation_match)})
                  </Field>
                  <Field label="Support">
                    {decision.q1_what_happened.supporting_well_count} wells ·{' '}
                    {decision.q1_what_happened.supporting_event_count} events
                  </Field>
                  <Field label="Distance to interval">{fmtMeters(alert.distance_to_interval_m, 0)}</Field>
                </dl>
                {decision.q1_what_happened.events.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {decision.q1_what_happened.events.map((event) => (
                      <button
                        key={event.event_id}
                        type="button"
                        onClick={() => openEvidence(event.event_id)}
                        title={`${event.well_id} · ${fmtMeters(event.md)} MD / ${fmtMeters(event.tvd)} TVD`}
                        className="tnum rounded-sm border border-line-strong bg-surface-1 px-1.5 py-px text-2xs text-accent hover:border-accent-line"
                      >
                        {event.event_id} →
                      </button>
                    ))}
                  </div>
                )}
              </Question>

              {/* Q2 */}
              <Question index="Q2" title="Why relevant now">
                <GeneratedList items={decision.q2_why_relevant_now} onOpenSource={openEvidence} />
              </Question>

              {/* Q3 */}
              <Question index="Q3" title="Supporting offset wells">
                {decision.q3_supporting_wells.length === 0 ? (
                  <EmptyState title="No supporting wells" hint="The rule did not return supporting offset wells." />
                ) : (
                  <ul className="space-y-1.5">
                    {decision.q3_supporting_wells.map((item) => (
                      <li key={item.well_id} className="rounded-sm border border-line px-2 py-1.5">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="tnum text-xs font-semibold text-fg-strong">{item.well_id}</span>
                          <Badge tone={relevanceTone(item.relevance_score)}>
                            {item.relevance_band} · {fmtPercent(item.relevance_score, 0)}
                          </Badge>
                          <span className="tnum text-2xs text-fg-muted">{fmtKm(item.distance_km)}</span>
                          <Badge tone="muted">{item.event_count} event(s)</Badge>
                        </div>
                        {item.why_relevant.length > 0 && (
                          <p className="mt-0.5 text-2xs text-fg-muted">
                            {uniqueReasons(item.why_relevant, 2).join(' · ')}
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </Question>

              {/* Q4 */}
              <Question index="Q4" title="Evidence">
                {decision.q4_evidence.length === 0 ? (
                  <EmptyState
                    title="No evidence excerpts"
                    hint="The alert carries no stored document excerpt in the record."
                  />
                ) : (
                  <ul className="space-y-1.5">
                    {decision.q4_evidence.map((item) => (
                      <li key={item.evidence_id} className="rounded-sm border border-line px-2 py-1.5">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="text-xs font-semibold text-fg-strong">
                            {item.document_ref ?? 'Unlinked excerpt'}
                          </span>
                          <Badge tone={item.page === null ? 'warning' : 'muted'}>{fmtPage(item.page)}</Badge>
                          <Badge tone="muted">{item.well_id}</Badge>
                          <Badge tone="info">{item.alert_id}</Badge>
                        </div>
                        <blockquote className="mt-1 border-l-2 border-accent-line bg-surface-2 px-2 py-1 text-xs leading-relaxed text-fg">
                          {item.text_span}
                        </blockquote>
                        <p className="mt-1 text-2xs text-fg-muted">
                          {item.well_name} · {item.section} · confidence {fmtPercent(item.confidence, 0)} ·{' '}
                          {item.extraction_method} ·{' '}
                          <button
                            type="button"
                            onClick={() => openEvidence(item.event_id)}
                            className="tnum text-accent underline underline-offset-2"
                          >
                            {item.event_id}
                          </button>
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </Question>

              {/* Q5 */}
              <Question index="Q5" title="Historical mitigation">
                <GeneratedList items={decision.q5_historical_mitigation} onOpenSource={openEvidence} />
              </Question>

              {/* Q6 */}
              <Question index="Q6" title="What to review">
                <GeneratedList items={decision.q6_what_to_review} onOpenSource={openEvidence} />
              </Question>

              {/* Evidence chain */}
              <section className="px-3 py-2">
                <h3 className="text-2xs font-semibold uppercase tracking-[0.08em] text-fg-strong">Evidence chain</h3>
                <ol className="mt-1.5 space-y-1">
                  {CHAIN_STEP_ORDER.map((step) => {
                    const matches = data.evidence_chain.filter((entry) => entry.step === step)
                    if (matches.length === 0) return null
                    return (
                      <li key={step} className="flex gap-2">
                        <span className="mt-px w-[74px] shrink-0 rounded-sm border border-line-strong bg-surface-2 px-1 py-px text-center text-2xs font-semibold tracking-[0.06em] text-fg-strong">
                          {step}
                        </span>
                        <ul className="min-w-0 flex-1 space-y-0.5">
                          {matches.map((entry) => (
                            <li key={`${entry.step}-${entry.ref}`} className="text-2xs leading-snug">
                              <span className="tnum font-semibold text-fg-strong">{entry.ref}</span>{' '}
                              <span className="text-fg-muted">{entry.detail}</span>
                            </li>
                          ))}
                        </ul>
                      </li>
                    )
                  })}
                </ol>
                {data.supporting_events.length > 0 && (
                  <p className="mt-1.5 text-2xs text-fg-muted">
                    Supporting events:{' '}
                    {data.supporting_events.map((event) => (
                      <button
                        key={event.id}
                        type="button"
                        onClick={() => openEvidence(event.id)}
                        className="tnum mr-1.5 text-accent underline underline-offset-2"
                      >
                        {event.id}
                      </button>
                    ))}
                  </p>
                )}
              </section>

              {/* Engineer actions */}
              <section className="px-3 py-2">
                <h3 className="text-2xs font-semibold uppercase tracking-[0.08em] text-fg-strong">
                  Engineer actions
                </h3>
                <div className="mt-1.5 grid grid-cols-[1fr_2fr] gap-1.5">
                  <input
                    value={engineer}
                    onChange={(event) => setEngineer(event.target.value)}
                    placeholder="Engineer"
                    aria-label="Engineer name"
                    className="h-7 rounded-sm border border-line-strong bg-surface-1 px-2 text-xs"
                  />
                  <input
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                    placeholder="Action note (recorded on the alert)"
                    aria-label="Engineer note"
                    className="h-7 rounded-sm border border-line-strong bg-surface-1 px-2 text-xs"
                  />
                </div>

                <div className="mt-2 flex flex-wrap gap-1.5">
                  <ActionButton onClick={() => void runAction('acknowledge')} disabled={submitting !== null}>
                    {submitting === 'acknowledge' ? 'ACK…' : '[ACKNOWLEDGE]'}
                  </ActionButton>
                  <ActionButton onClick={() => void runAction('note')} disabled={submitting !== null}>
                    {submitting === 'note' ? 'POST…' : '[ADD ENGINEER NOTE]'}
                  </ActionButton>
                  <ActionButton
                    onClick={() => {
                      closeDecision()
                      navigate(`/offset-intelligence/replay?well=${alert.current_well_id}&alert=${alert.id}`)
                    }}
                  >
                    [VIEW OFFSET WELLS]
                  </ActionButton>
                  <ActionButton
                    disabled={!firstLinkedEventId}
                    onClick={() => {
                      if (!firstLinkedEventId) return
                      closeDecision()
                      openEvidence(firstLinkedEventId)
                    }}
                  >
                    [VIEW EVIDENCE]
                  </ActionButton>
                </div>

                <p className="mt-1.5 text-2xs text-fg-muted">
                  Actions post to the acknowledge / notes endpoints and refresh this panel. Recorded as{' '}
                  {humanizeEnum(role)} view.
                </p>

                {data.actions.length > 0 && (
                  <ul className="mt-2 divide-y divide-line border-t border-line">
                    {[...data.actions].reverse().map((action) => (
                      <li key={action.id} className="py-1.5">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Badge tone={action.action_type === 'ACKNOWLEDGE' ? 'success' : 'info'}>
                            {humanizeEnum(action.action_type)}
                          </Badge>
                          <span className="text-xs font-semibold text-fg-strong">{action.engineer}</span>
                          <span className="text-2xs text-fg-muted">{fmtDateTime(action.created_at)}</span>
                        </div>
                        {action.note && <p className="mt-0.5 text-xs text-fg">{action.note}</p>}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <footer className="px-3 py-2 text-2xs text-fg-muted">
                Real public data · rule {data.rule.rule_id} v{data.rule.version} · method{' '}
                {data.rule.method}. Heuristic output, not an operational instruction.
              </footer>
            </div>
          )}
        </div>
      </aside>
    </div>
  )
}

const CHAIN_LABELS = [
  'historical_event_match',
  'depth_proximity',
  'formation_similarity',
  'nearby_well_support',
  'operational_similarity',
] as const

function Question({ index, title, children }: { index: string; title: string; children: ReactNode }) {
  return (
    <section className="px-3 py-2">
      <h3 className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-[0.08em] text-fg-strong">
        <span className="rounded-sm border border-line-strong bg-accent-soft px-1 py-px text-fg-strong">{index}</span>
        {title}
      </h3>
      <div className={cx('mt-1')}>{children}</div>
    </section>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-2xs uppercase tracking-[0.06em] text-fg-muted">{label}</dt>
      <dd className="truncate text-xs text-fg">{children}</dd>
    </div>
  )
}

function ActionButton({
  children,
  onClick,
  disabled,
}: {
  children: ReactNode
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex h-7 items-center rounded-sm border border-accent bg-accent px-2.5 text-2xs font-semibold uppercase tracking-[0.06em] text-accent-fg hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {children}
    </button>
  )
}
