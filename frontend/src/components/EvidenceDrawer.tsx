import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { Api } from '@/api/client'
import type { EvidenceBrief, EvidenceResponse } from '@/api/types'
import { useAsyncData } from '@/lib/useAsyncData'
import {
  PAGE_UNAVAILABLE,
  bandTone,
  eventSeverityDotClass,
  fmtDate,
  fmtMeters,
  fmtPage,
  fmtPercent,
  humanizeEnum,
} from '@/lib/format'
import { useApp } from '@/store/useApp'
import { Badge, DataTable, EmptyState, Skeleton, cx, type DataTableColumn } from './primitives'
import { LineIcon } from './LineIcon'

/**
 * Evidence audit chain drawer. Mounted once, globally, by the AppShell: any
 * screen calls `useApp().openEvidence(eventId)` and this drawer opens.
 *
 * Audit chain is rendered strictly in the order the contract defines:
 * Alert → Reason → Event → Well → Document → Evidence.
 */
export function EvidenceDrawer({ eventId, onClose }: { eventId: string; onClose?: () => void }) {
  const { closeEvidence, openEvidence, notify } = useApp()
  const navigate = useNavigate()
  const [chainCollapsed, setChainCollapsed] = useState(false)

  const { data, error, loading, reload } = useAsyncData<EvidenceResponse>(
    (signal) => Api.evidence(eventId, signal),
    [eventId],
  )

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') (onClose ?? closeEvidence)()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, closeEvidence])

  const event = data?.event
  const sourceDocument = data?.document ?? null
  const evidence: EvidenceBrief[] = data?.evidence ?? []

  const neighbourColumns: DataTableColumn<{ id: string; relation: string; label: string; detail: string }>[] = [
    { key: 'relation', header: 'Adjacent', width: 92 },
    { key: 'label', header: 'Event', render: (row) => <span className="text-fg-strong">{row.label}</span> },
    { key: 'detail', header: 'TVD', align: 'right', render: (row) => <span className="tnum">{row.detail}</span> },
  ]

  const neighbours = [
    data?.context.previous_event
      ? {
          id: data.context.previous_event.id,
          relation: 'Previous',
          label: data.context.previous_event.event_label,
          detail: fmtMeters(data.context.previous_event.tvd),
        }
      : null,
    data?.context.next_event
      ? {
          id: data.context.next_event.id,
          relation: 'Next',
          label: data.context.next_event.event_label,
          detail: fmtMeters(data.context.next_event.tvd),
        }
      : null,
  ].filter((row): row is NonNullable<typeof row> => row !== null)

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Evidence audit chain">
      <button
        type="button"
        aria-label="Close evidence drawer"
        onClick={onClose ?? closeEvidence}
        className="absolute inset-0 bg-chrome/40"
      />

      <aside className="relative flex h-full w-full max-w-[560px] flex-col border-l border-line-strong bg-surface-1 shadow-[var(--shadow-raised)]">
        <header className="flex items-start justify-between gap-3 border-b border-line bg-chrome px-3 py-2 text-chrome-fg">
          <div className="min-w-0">
            <p className="text-2xs font-semibold uppercase tracking-[0.1em] text-chrome-muted">
              Evidence · audit chain
            </p>
            <h2 className="truncate text-sm font-semibold">
              {event ? `${event.id} · ${event.event_label}` : eventId}
            </h2>
            {event && (
              <p className="truncate text-2xs text-chrome-muted">
                {event.well_name} · MD {fmtMeters(event.md)} · TVD {fmtMeters(event.tvd)} · {event.formation}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose ?? closeEvidence}
            className="shrink-0 rounded-sm border border-chrome-2 p-1 text-chrome-fg hover:bg-chrome-2"
            aria-label="Close"
          >
            <LineIcon name="close" size={14} />
          </button>
        </header>

        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
          {loading && !data && (
            <div className="space-y-2 p-3">
              <Skeleton className="h-3 w-2/3" />
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-3 w-1/2" />
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

          {data && event && (
            <div className="divide-y divide-line">
              {/* Event header */}
              <section className="px-3 py-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="flex items-center gap-1 text-xs font-semibold text-fg-strong">
                    <span className={cx('size-2 rounded-full', eventSeverityDotClass(event.severity))} />
                    {event.event_label}
                  </span>
                  <Badge tone={bandTone(event.severity === 'CRITICAL' ? 'CRITICAL' : 'INFO')}>
                    {humanizeEnum(event.severity)}
                  </Badge>
                  <Badge tone="muted">{humanizeEnum(event.event_type)}</Badge>
                  {event.evidence_count > 0 && <Badge tone="info">{event.evidence_count} evidence</Badge>}
                </div>
                <p className="mt-1 text-xs leading-snug text-fg">{event.description}</p>
                {event.mitigation && (
                  <p className="mt-1 text-xs leading-snug text-fg-muted">
                    <span className="font-semibold text-fg-strong">Mitigation:</span> {event.mitigation}
                  </p>
                )}
                <p className="mt-1 text-2xs text-fg-muted">
                  {event.well_id} · occurred {fmtDate(event.occurred_at)} · provenance {event.data_provenance}
                </p>
              </section>

              {/* Audit chain */}
              <section>
                <button
                  type="button"
                  onClick={() => setChainCollapsed((value) => !value)}
                  className="flex w-full items-center justify-between bg-surface-2 px-3 py-1.5 text-left hover:bg-surface-3"
                >
                  <span className="text-2xs font-semibold uppercase tracking-[0.08em] text-fg-strong">
                    Audit chain · Alert → Reason → Event → Well → Document → Evidence
                  </span>
                  <span className="text-2xs text-fg-muted">{chainCollapsed ? '▸' : '▾'}</span>
                </button>

                {!chainCollapsed && (
                  <ol className="divide-y divide-line">
                    <ChainRow
                      step="ALERT"
                      ref={data.chain.alert_ids.length ? data.chain.alert_ids.join(', ') : '—'}
                      detail={
                        data.chain.alert_ids.length
                          ? 'Alert(s) raised by the risk engine for this event.'
                          : 'No alert references this event in the record.'
                      }
                    />
                    {data.chain.reasons.length === 0 ? (
                      <ChainRow step="REASON" ref="—" detail="No rule reason stored for this event." />
                    ) : (
                      data.chain.reasons.map((reason) => (
                        <ChainRow
                          key={`${reason.alert_id}-${reason.reason}`}
                          step="REASON"
                          ref={reason.alert_id}
                          detail={reason.reason}
                        />
                      ))
                    )}
                    <ChainRow
                      step="EVENT"
                      ref={event.id}
                      detail={`${humanizeEnum(event.event_type)} at MD ${fmtMeters(event.md)} / TVD ${fmtMeters(event.tvd)} in ${event.formation}.`}
                    />
                    <ChainRow
                      step="WELL"
                      ref={data.chain.well.id}
                      detail={`${data.chain.well.name} · ${data.chain.well.field} · status ${humanizeEnum(data.chain.well.status)}.`}
                    />
                    <ChainRow
                      step="DOCUMENT"
                      ref={sourceDocument ? sourceDocument.id : '—'}
                      detail={
                        sourceDocument
                          ? `${sourceDocument.doc_type_label} · ${sourceDocument.filename} · dated ${fmtDate(sourceDocument.doc_date)}.`
                          : 'No source document linked in the record.'
                      }
                    />
                    {evidence.length === 0 ? (
                      <ChainRow step="EVIDENCE" ref="—" detail="No stored evidence excerpt for this event." />
                    ) : (
                      evidence.map((item) => (
                        <ChainRow
                          key={item.id}
                          step="EVIDENCE"
                          ref={item.id}
                          detail={`${item.section} · ${fmtPage(item.page)} · confidence ${fmtPercent(item.confidence, 0)} · ${item.extraction_method}.`}
                        />
                      ))
                    )}
                  </ol>
                )}
              </section>

              {/* Document metadata */}
              <section className="px-3 py-2">
                <h3 className="text-2xs font-semibold uppercase tracking-[0.08em] text-fg-strong">
                  Source document
                </h3>
                {sourceDocument ? (
                  <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
                    <Field label="Document">{sourceDocument.id}</Field>
                    <Field label="Type">{sourceDocument.doc_type_label}</Field>
                    <Field label="Title">{sourceDocument.title}</Field>
                    <Field label="File">{sourceDocument.filename}</Field>
                    <Field label="Dated">{fmtDate(sourceDocument.doc_date)}</Field>
                    <Field label="Pages">{sourceDocument.page_count}</Field>
                    <Field label="Page of excerpt">
                      <span className={cx(!evidence[0] || evidence[0].page === null ? 'text-warn' : undefined)}>
                        {evidence[0] ? fmtPage(evidence[0].page) : PAGE_UNAVAILABLE}
                      </span>
                    </Field>
                    <Field label="Source system">{sourceDocument.source_system}</Field>
                  </dl>
                ) : (
                  <p className="mt-1 text-xs text-fg-muted">No source document linked in the record.</p>
                )}

                <div className="mt-2 flex flex-wrap gap-1.5">
                  <button
                    type="button"
                    disabled={!sourceDocument}
                    onClick={() => {
                      if (!sourceDocument) return
                      ;(onClose ?? closeEvidence)()
                      navigate(`/documents/${sourceDocument.id}?well=${event.well_id}`)
                    }}
                    className="inline-flex h-7 items-center gap-1 rounded-sm border border-accent bg-accent px-2 text-2xs font-semibold uppercase tracking-[0.06em] text-accent-fg hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <LineIcon name="open" size={12} /> [OPEN SOURCE]
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      ;(onClose ?? closeEvidence)()
                      navigate(`/events/timeline?well=${event.well_id}&event=${event.id}`)
                    }}
                    className="inline-flex h-7 items-center gap-1 rounded-sm border border-line-strong bg-surface-1 px-2 text-2xs font-semibold uppercase tracking-[0.06em] text-fg hover:border-accent-line"
                  >
                    <LineIcon name="timeline" size={12} /> [VIEW CONTEXT]
                  </button>
                </div>
              </section>

              {/* Stored excerpts */}
              <section>
                <h3 className="bg-surface-2 px-3 py-1.5 text-2xs font-semibold uppercase tracking-[0.08em] text-fg-strong">
                  Stored text span ({evidence.length})
                </h3>
                {evidence.length === 0 ? (
                  <EmptyState title="No stored evidence" hint="The record carries no excerpt for this event." />
                ) : (
                  <ul className="divide-y divide-line">
                    {evidence.map((item) => (
                      <li key={item.id} className="px-3 py-2">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="text-xs font-semibold text-fg-strong">{item.section}</span>
                          <Badge tone={item.page === null ? 'warning' : 'muted'}>{fmtPage(item.page)}</Badge>
                          <Badge tone="muted">confidence {fmtPercent(item.confidence, 0)}</Badge>
                        </div>
                        <blockquote className="mt-1 border-l-2 border-accent-line bg-surface-2 px-2 py-1.5 text-xs leading-relaxed text-fg">
                          {item.text_span}
                        </blockquote>
                        <p className="mt-1 text-2xs text-fg-muted">
                          {item.id} · extraction {item.extraction_method} · bounding box{' '}
                          {item.bbox === null ? 'not captured in the record' : `[${item.bbox.join(', ')}]`}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {/* Context */}
              <section>
                <h3 className="bg-surface-2 px-3 py-1.5 text-2xs font-semibold uppercase tracking-[0.08em] text-fg-strong">
                  Context in the well
                </h3>
                {neighbours.length === 0 ? (
                  <p className="px-3 py-2 text-xs text-fg-muted">
                    No adjacent events stored for this well in the record.
                  </p>
                ) : (
                  <DataTable
                    className="mt-0"
                    columns={neighbourColumns}
                    rows={neighbours}
                    rowKey={(row) => row.id}
                    onRowClick={(row) => {
                      openEvidence(row.id)
                      notify(`Loaded evidence for ${row.id}`, 'info')
                    }}
                  />
                )}
                {data.context.document_excerpt && (
                  <div className="px-3 py-2">
                    <h4 className="text-2xs font-semibold uppercase tracking-[0.08em] text-fg-strong">
                      Document excerpt
                    </h4>
                    <p className="mt-1 max-h-40 overflow-y-auto border-l-2 border-accent-line bg-surface-2 px-2 py-1.5 text-xs leading-relaxed text-fg">
                      {data.context.document_excerpt}
                    </p>
                  </div>
                )}
              </section>

              <footer className="px-3 py-2 text-2xs text-fg-muted">
                Real public data · audit chain rendered from API records only. No page, line or
                source metadata is inferred in the UI.
              </footer>
            </div>
          )}
        </div>
      </aside>
    </div>
  )
}

function ChainRow({ step, ref, detail }: { step: string; ref: string; detail: string }) {
  return (
    <li className="flex gap-2 px-3 py-1.5">
      <span className="mt-px w-[74px] shrink-0 rounded-sm border border-line-strong bg-surface-2 px-1 py-px text-center text-2xs font-semibold tracking-[0.06em] text-fg-strong">
        {step}
      </span>
      <div className="min-w-0 flex-1">
        <p className="tnum truncate text-xs font-semibold text-fg-strong">{ref}</p>
        <p className="text-2xs leading-snug text-fg-muted">{detail}</p>
      </div>
    </li>
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
