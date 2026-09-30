import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Api } from '@/api/client'
import type { EventType, SearchRequest, SearchResponse } from '@/api/types'
import {
  Badge,
  Callout,
  DataTable,
  Disclaimer,
  Disclosure,
  EmptyState,
  KeyValueGrid,
  LineIcon,
  ProvenanceTag,
  ScreenHeader,
  SectionCard,
  Toolbar,
  actionButtonClass,
  controlClass,
  cx,
  ghostButtonClass,
} from '@/components/ui'
import { useApp } from '@/store/useApp'
import {
  bandTone,
  fmtDate,
  fmtMeters,
  fmtNumber,
  fmtPage,
  fmtPercent,
  humanizeEnum,
  severityTone,
} from '@/lib/format'
import { LABEL, severityFillVar } from './_shared'
import { SliceNote, useBounded } from './_calm'

/* ------------------------------------------------------------------ *
 * Screen 7 — Knowledge search
 *
 * Structured retrieval is the ANSWER; the synthesis block is a labelled
 * secondary artefact. A long generated paragraph is never the primary output:
 * the query bar, how the query was interpreted, and the matched event records
 * are. Every other record type is one click away, behind a counted
 * Disclosure, so the first screen is a result set and not a wall.
 * ------------------------------------------------------------------ */

/* Real formations in the corpus are NPD group names (Hordaland Gp., Skagerrak
   Fm., Zechstein Gp.) — the old "Barail" example was synthetic-era and matches
   nothing. */
const EXAMPLE_QUERIES = [
  'What happened around 500 m TVD?',
  'Show me stuck pipe events in the Hordaland formation',
  'Which offset wells had mud loss near the current well?',
  'What mitigations were used for lost circulation?',
] as const

export function Search() {
  const { meta, currentWellId, openEvidence, openDecision } = useApp()
  const [searchParams, setSearchParams] = useSearchParams()

  const [query, setQuery] = useState(searchParams.get('q') ?? '')
  const [radiusKm, setRadiusKm] = useState(8)
  const [eventType, setEventType] = useState<EventType | ''>('')
  const [formation, setFormation] = useState('')
  const [tvdMin, setTvdMin] = useState('')
  const [tvdMax, setTvdMax] = useState('')
  const [limit, setLimit] = useState(20)
  const [result, setResult] = useState<SearchResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitted, setSubmitted] = useState(Boolean(searchParams.get('q')))

  const run = useCallback(
    async (text: string) => {
      const trimmed = text.trim()
      if (!trimmed) return
      setLoading(true)
      setError(null)
      try {
        const body: SearchRequest = {
          query: trimmed,
          current_well_id: currentWellId,
          filters: {
            radius_km: radiusKm,
            event_type: eventType === '' ? null : eventType,
            formation: formation === '' ? null : formation,
            tvd_min: tvdMin.trim() === '' ? null : Number(tvdMin),
            tvd_max: tvdMax.trim() === '' ? null : Number(tvdMax),
          },
          limit,
        }
        setResult(await Api.search(body))
        setSubmitted(true)
        const params = new URLSearchParams(searchParams)
        params.set('q', trimmed)
        setSearchParams(params, { replace: true })
      } catch (err) {
        setResult(null)
        setError(err instanceof Error ? err.message : 'Search request failed')
      } finally {
        setLoading(false)
      }
    },
    [
      currentWellId,
      radiusKm,
      eventType,
      formation,
      tvdMin,
      tvdMax,
      limit,
      searchParams,
      setSearchParams,
    ],
  )

  /* Deep link: /search?q=… runs once on mount. */
  useEffect(() => {
    const initial = searchParams.get('q')
    if (initial) void run(initial)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const grouped = result?.structured_results
  const totalGroups = grouped
    ? grouped.events.length +
      grouped.wells.length +
      grouped.documents.length +
      grouped.mitigations.length +
      grouped.evidence.length +
      grouped.alerts.length
    : 0

  const events = useBounded(grouped?.events ?? [], 20, 40)
  const wells = useBounded(grouped?.wells ?? [], 8, 20)
  const documents = useBounded(grouped?.documents ?? [], 8, 20)
  const mitigations = useBounded(grouped?.mitigations ?? [], 6, 12)
  const excerpts = useBounded(grouped?.evidence ?? [], 6, 12)

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    void run(query)
  }

  return (
    <div className="flex flex-col gap-4">
      <ScreenHeader
        title="Knowledge search"
        subtitle="Hybrid structured + semantic retrieval over events, wells, documents, mitigations and evidence. Structured records are the answer; the generated summary is a labelled secondary artefact."
        meta={
          <>
            <Badge tone="warning">{meta?.dataset_label ?? 'Real public data'}</Badge>
            {result ? <Badge tone="neutral">{fmtNumber(result.result_count, 0)} records</Badge> : null}
          </>
        }
      />

      {/* ---------------- HERO — query bar + interpretation ---------------- */}
      <SectionCard title="Query" description="Ask about a depth, an event type, a formation or an offset well." dense>
        <form onSubmit={onSubmit}>
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="relative flex-1">
              <span className="pointer-events-none absolute inset-y-0 left-2.5 flex items-center text-fg-subtle">
                <LineIcon name="search" size={15} />
              </span>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="e.g. What happened around 1500 m TVD?"
                aria-label="Search query"
                className={cx(controlClass, 'h-9 w-full pl-8 text-sm')}
              />
            </div>
            <button
              type="submit"
              className={cx(actionButtonClass, 'h-9 px-4')}
              disabled={loading || !query.trim()}
            >
              <LineIcon name="search" size={13} />
              {loading ? 'Searching…' : 'Search'}
            </button>
          </div>

          <div className="mt-2.5 flex flex-wrap gap-1.5">
            <span className="self-center text-2xs uppercase tracking-wider text-fg-subtle">Try</span>
            {EXAMPLE_QUERIES.map((q) => (
              <button
                key={q}
                type="button"
                className={cx(ghostButtonClass, 'h-6 normal-case tracking-normal')}
                onClick={() => {
                  setQuery(q)
                  void run(q)
                }}
              >
                {q}
              </button>
            ))}
          </div>
        </form>

        <Toolbar className="-mx-2 mt-3 flex flex-wrap items-end gap-x-3 gap-y-3 rounded-t-md border-t">
          <div className="w-28">
            <label className={LABEL} htmlFor="q-radius">
              Radius (km)
            </label>
            <select
              id="q-radius"
              className={cx(controlClass, 'mt-1 w-full')}
              value={radiusKm}
              onChange={(e) => setRadiusKm(Number(e.target.value))}
            >
              {[2, 4, 8, 12, 20, 40].map((v) => (
                <option key={v} value={v}>
                  {v} km
                </option>
              ))}
            </select>
          </div>
          <div className="w-44">
            <label className={LABEL} htmlFor="q-type">
              Event type
            </label>
            <select
              id="q-type"
              className={cx(controlClass, 'mt-1 w-full')}
              value={eventType}
              onChange={(e) => setEventType(e.target.value as EventType | '')}
            >
              <option value="">Any</option>
              {(meta?.event_types ?? []).map((t) => (
                <option key={t.code} value={t.code}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>
          <div className="w-36">
            <label className={LABEL} htmlFor="q-formation">
              Formation
            </label>
            <input
              id="q-formation"
              className={cx(controlClass, 'mt-1 w-full')}
              placeholder="e.g. Barail"
              value={formation}
              onChange={(e) => setFormation(e.target.value)}
            />
          </div>
          <div className="w-28">
            <label className={LABEL} htmlFor="q-tvdmin">
              TVD min (m)
            </label>
            <input
              id="q-tvdmin"
              inputMode="numeric"
              className={cx(controlClass, 'tnum mt-1 w-full')}
              value={tvdMin}
              onChange={(e) => setTvdMin(e.target.value.replace(/[^\d.]/g, ''))}
            />
          </div>
          <div className="w-28">
            <label className={LABEL} htmlFor="q-tvdmax">
              TVD max (m)
            </label>
            <input
              id="q-tvdmax"
              inputMode="numeric"
              className={cx(controlClass, 'tnum mt-1 w-full')}
              value={tvdMax}
              onChange={(e) => setTvdMax(e.target.value.replace(/[^\d.]/g, ''))}
            />
          </div>
          <div className="w-28">
            <label className={LABEL} htmlFor="q-limit">
              Max results
            </label>
            <select
              id="q-limit"
              className={cx(controlClass, 'mt-1 w-full')}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
            >
              {[10, 20, 50, 100].map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </div>
        </Toolbar>

        {error ? (
          <div className="mt-3">
            <Callout tone="critical" title="Search unavailable" icon={<LineIcon name="warning" size={14} />}>
              {error}
            </Callout>
          </div>
        ) : null}

        {result ? (
          <div className="mt-3 border-t border-line pt-3">
            <h3 className="text-2xs font-semibold uppercase tracking-[0.07em] text-fg-muted">
              How your query was interpreted
            </h3>
            <div className="mt-1.5 grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
              <div>
                <p className="text-xs leading-relaxed text-fg">{result.parsed_intent.explain}</p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <Badge tone="info">{humanizeEnum(result.parsed_intent.intent)}</Badge>
                  {result.parsed_intent.near_current_well ? (
                    <Badge tone="success">scoped to the current well</Badge>
                  ) : null}
                  {result.parsed_intent.event_type.map((t) => (
                    <Badge key={t} tone="neutral">
                      {humanizeEnum(t)}
                    </Badge>
                  ))}
                  {result.parsed_intent.formations.map((f) => (
                    <Badge key={f} tone="neutral">
                      {f}
                    </Badge>
                  ))}
                </div>
                {/* Only what the PARSER inferred — the request filters are
                    already visible in the bar above, so restating them as
                    badges was the same fact said twice. */}
                <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-line pt-2">
                  <span className="text-2xs uppercase tracking-wider text-fg-subtle">
                    Inferred by the parser
                  </span>
                  {result.parsed_intent.radius_km !== null ? (
                    <Badge tone="neutral">{`radius ${result.parsed_intent.radius_km} km`}</Badge>
                  ) : null}
                  {result.parsed_intent.tvd_anchor_m !== null ? (
                    <Badge tone="neutral">
                      {`TVD anchor ${fmtMeters(result.parsed_intent.tvd_anchor_m)}`}
                    </Badge>
                  ) : null}
                  <Badge tone="neutral">{`limit ${limit}`}</Badge>
                </div>
              </div>
              <KeyValueGrid
                className="lg:border-l lg:border-line lg:pl-3"
                items={[
                  {
                    label: 'TVD anchor',
                    mono: true,
                    value:
                      result.parsed_intent.tvd_anchor_m === null
                        ? 'none detected'
                        : fmtMeters(result.parsed_intent.tvd_anchor_m),
                  },
                  {
                    label: 'Radius',
                    mono: true,
                    value:
                      result.parsed_intent.radius_km === null
                        ? 'engine default'
                        : `${result.parsed_intent.radius_km} km`,
                  },
                  {
                    label: 'Records returned',
                    mono: true,
                    value: fmtNumber(result.result_count, 0),
                  },
                  { label: 'Provenance', mono: true, value: result.data_provenance },
                ]}
              />
            </div>
          </div>
        ) : null}
      </SectionCard>

      {!submitted && !loading && !result ? (
        <SectionCard>
          <EmptyState
            icon={<LineIcon name="search" size={22} />}
            title="Search the drilling knowledge base"
            hint="The engine parses the intent, then returns the matching records grouped by type — events first, everything else one click away."
          />
        </SectionCard>
      ) : null}

      {result && totalGroups === 0 ? (
        <SectionCard>
          <EmptyState
            icon={<LineIcon name="search" size={22} />}
            title="No records matched this query"
            hint="The intent parser found a readable intent but the retrieval step matched nothing in the dataset. Try a broader phrasing:"
          />
          <div className="grid grid-cols-1 gap-2 border-t border-line px-3 py-3 md:grid-cols-2">
            {EXAMPLE_QUERIES.map((q) => (
              <button
                key={q}
                type="button"
                className="panel-surface flex items-center gap-2 px-3 py-2 text-left text-xs text-fg transition-colors hover:border-accent-line hover:text-accent"
                onClick={() => {
                  setQuery(q)
                  void run(q)
                }}
              >
                <LineIcon name="search" size={13} />
                {q}
              </button>
            ))}
          </div>
        </SectionCard>
      ) : null}

      {result && grouped && grouped.events.length > 0 ? (
        <SectionCard
          title="Events"
          description="The primary result set. Click a row to open its evidence chain."
          actions={
            <span className="tnum text-2xs text-fg-muted">
              {`${events.shown} of ${grouped.events.length}`}
            </span>
          }
          dense
        >
          <DataTable
            rows={events.visible}
            rowKey={(e) => e.id}
            onRowClick={(e) => openEvidence(e.id)}
            columns={[
              {
                key: 'event',
                header: 'Event',
                render: (e) => (
                  <span className="flex items-center gap-1.5">
                    <span
                      className="h-2 w-2 shrink-0 rounded-sm"
                      style={{ backgroundColor: severityFillVar(e.severity) }}
                    />
                    <span className="min-w-0">
                      <span className="block truncate font-medium text-fg-strong">
                        {e.event_label}
                      </span>
                      <span className="tnum block text-2xs text-fg-subtle">{e.id}</span>
                    </span>
                  </span>
                ),
              },
              {
                key: 'well',
                header: 'Well',
                render: (e) => <span className="whitespace-nowrap">{e.well_name}</span>,
              },
              {
                key: 'tvd',
                header: 'TVD',
                align: 'right',
                render: (e) => <span className="tnum">{fmtMeters(e.tvd)}</span>,
              },
              { key: 'formation', header: 'Formation', render: (e) => e.formation || '—' },
              {
                key: 'severity',
                header: 'Severity',
                align: 'right',
                render: (e) => <Badge tone={severityTone(e.severity)}>{e.severity}</Badge>,
              },
              {
                key: 'document',
                header: 'Document',
                render: (e) =>
                  e.document ? (
                    <span className="block min-w-0">
                      <span className="block truncate text-2xs text-fg">{e.document.filename}</span>
                      <span className="block text-2xs text-fg-subtle">
                        {e.document.doc_type_label}
                      </span>
                    </span>
                  ) : (
                    <span className="text-2xs text-fg-subtle">none attached</span>
                  ),
              },
              {
                key: 'mitigation',
                header: 'Mitigation',
                render: (e) => (
                  <span className="line-clamp-2 text-2xs text-fg-muted">{e.mitigation || '—'}</span>
                ),
              },
            ]}
          />
          <SliceNote
            className="mt-2"
            shown={events.shown}
            total={grouped.events.length}
            noun="events"
            onMore={events.more}
          />
        </SectionCard>
      ) : null}

      {grouped ? (
        <div className="flex flex-col gap-3">
          <Disclosure
            summary={`Wells — ${grouped.wells.length}`}
            badge={grouped.wells.length > 0 ? undefined : <Badge tone="muted">empty</Badge>}
          >
            <p className="mb-2 text-2xs text-fg-subtle">
              Relevance and why-factors come from the engine.
            </p>
            <DataTable
              rows={wells.visible}
              rowKey={(w) => w.well.id}
              columns={[
                {
                  key: 'well',
                  header: 'Well',
                  render: (w) => (
                    <span>
                      <span className="block font-medium text-fg-strong">{w.well.name}</span>
                      <span className="tnum block text-2xs text-fg-subtle">{w.well.id}</span>
                    </span>
                  ),
                },
                {
                  key: 'distance',
                  header: 'Distance',
                  align: 'right',
                  render: (w) => <span className="tnum">{`${w.distance_km.toFixed(2)} km`}</span>,
                },
                {
                  key: 'relevance',
                  header: 'Relevance',
                  align: 'right',
                  render: (w) => (
                    <span className="tnum font-semibold">{fmtPercent(w.relevance_score, 0)}</span>
                  ),
                },
                {
                  key: 'band',
                  header: 'Band',
                  align: 'right',
                  render: (w) => <Badge tone={bandTone(w.relevance_band)}>{w.relevance_band}</Badge>,
                },
                {
                  key: 'formation',
                  header: 'Formation',
                  render: (w) => w.well.current_formation?.name ?? '—',
                },
                {
                  key: 'events',
                  header: 'Events',
                  align: 'right',
                  render: (w) => <span className="tnum">{w.event_count}</span>,
                },
                {
                  key: 'why',
                  header: 'Why relevant',
                  render: (w) => (
                    <span className="text-2xs text-fg-muted">{w.why_relevant.join(' · ')}</span>
                  ),
                },
              ]}
            />
            <SliceNote
              className="mt-2"
              shown={wells.shown}
              total={grouped.wells.length}
              noun="wells"
              onMore={wells.more}
            />
          </Disclosure>

          <Disclosure summary={`Documents — ${grouped.documents.length}`}>
            <DataTable
              rows={documents.visible}
              rowKey={(d) => d.id}
              columns={[
                {
                  key: 'document',
                  header: 'Document',
                  render: (d) => (
                    <span>
                      <span className="block truncate font-medium text-fg-strong">{d.title}</span>
                      <span className="block text-2xs text-fg-subtle">{d.filename}</span>
                    </span>
                  ),
                },
                { key: 'type', header: 'Type', render: (d) => d.doc_type_label },
                { key: 'well', header: 'Well', render: (d) => d.well_name ?? '—' },
                {
                  key: 'date',
                  header: 'Date',
                  align: 'right',
                  render: (d) => fmtDate(d.doc_date),
                },
                {
                  key: 'events',
                  header: 'Events',
                  align: 'right',
                  render: (d) => <span className="tnum">{d.event_count}</span>,
                },
                {
                  key: 'evidence',
                  header: 'Evidence',
                  align: 'right',
                  render: (d) => <span className="tnum">{d.evidence_count}</span>,
                },
              ]}
            />
            <SliceNote
              className="mt-2"
              shown={documents.shown}
              total={grouped.documents.length}
              noun="documents"
              onMore={documents.more}
            />
          </Disclosure>

          <Disclosure summary={`Historical mitigation — ${grouped.mitigations.length}`}>
            {grouped.mitigations.length === 0 ? (
              <p className="text-xs text-fg-subtle">No mitigation text matched this query.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {mitigations.visible.map((m) => (
                  <li
                    key={`${m.event_id}-${m.text.slice(0, 24)}`}
                    className="inset-surface px-2.5 py-2"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <ProvenanceTag provenance={m.provenance} />
                      <span className="tnum text-2xs text-fg-muted">{m.well_name}</span>
                      <span className="text-2xs text-fg-subtle">
                        {m.document_ref ?? 'no document reference'}
                      </span>
                      {m.event_id ? (
                        <button
                          type="button"
                          className="ml-auto text-2xs font-semibold text-accent hover:underline"
                          onClick={() => openEvidence(m.event_id)}
                        >
                          {m.event_id} →
                        </button>
                      ) : null}
                    </div>
                    <p className="mt-1 text-xs leading-relaxed text-fg">{m.text}</p>
                  </li>
                ))}
              </ul>
            )}
            <SliceNote
              className="mt-2"
              shown={mitigations.shown}
              total={grouped.mitigations.length}
              noun="mitigations"
              onMore={mitigations.more}
            />
          </Disclosure>

          <Disclosure summary={`Evidence — ${grouped.evidence.length}`}>
            {grouped.evidence.length === 0 ? (
              <p className="text-xs text-fg-subtle">No evidence excerpts matched this query.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {excerpts.visible.map((ev) => (
                  <li key={ev.id} className="inset-surface px-2.5 py-2">
                    <div className="flex flex-wrap items-center gap-2 text-2xs text-fg-muted">
                      <span className="tnum font-semibold text-fg">{ev.id}</span>
                      <Badge tone="neutral">{fmtPage(ev.page)}</Badge>
                      {ev.section ? <span>{ev.section}</span> : null}
                      <span className="tnum ml-auto">{`confidence ${ev.confidence.toFixed(2)}`}</span>
                    </div>
                    <p className="mt-1 border-l-2 border-line-strong pl-2 text-xs italic leading-relaxed text-fg">
                      {ev.text_span}
                    </p>
                    <p className="mt-1 text-2xs text-fg-subtle">{ev.extraction_method}</p>
                  </li>
                ))}
              </ul>
            )}
            <SliceNote
              className="mt-2"
              shown={excerpts.shown}
              total={grouped.evidence.length}
              noun="excerpts"
              onMore={excerpts.more}
            />
          </Disclosure>

          {grouped.alerts.length > 0 ? (
            <Disclosure summary={`Alerts — ${grouped.alerts.length}`}>
              <ul className="flex flex-col gap-2">
                {grouped.alerts.map((a) => (
                  <li
                    key={a.id}
                    className="inset-surface flex flex-wrap items-center gap-2 px-2.5 py-2"
                  >
                    <span className="tnum text-2xs font-semibold text-fg-muted">{a.id}</span>
                    <span className="text-xs font-medium text-fg-strong">{a.title}</span>
                    <Badge tone={bandTone(a.severity_band)}>{a.severity_band}</Badge>
                    <span className="tnum text-2xs text-fg-muted">
                      {`${a.current_well_name} · risk ${a.risk_score.toFixed(2)}`}
                    </span>
                    <button
                      type="button"
                      className={cx(ghostButtonClass, 'ml-auto h-6')}
                      onClick={() => openDecision(a.id)}
                    >
                      Decision panel
                    </button>
                  </li>
                ))}
              </ul>
            </Disclosure>
          ) : null}
        </div>
      ) : null}

      {result ? (
        <Disclosure
          summary={
            <span className="flex items-center gap-2">
              <span>Generated summary</span>
              <ProvenanceTag
                provenance={result.synthesis.provenance}
                model={result.synthesis.model}
              />
            </span>
          }
          badge={<Badge tone="warning">secondary</Badge>}
        >
          <p className="text-xs leading-relaxed text-fg">{result.synthesis.text}</p>
          {result.synthesis.citations.length > 0 ? (
            <>
              <h4 className="mt-3 text-2xs font-semibold uppercase tracking-[0.07em] text-fg-muted">
                Citations
              </h4>
              <ul className="mt-1 flex flex-wrap gap-1.5">
                {result.synthesis.citations.map((c, i) => (
                  <li key={`${c.label}-${i}`}>
                    {c.event_id ? (
                      <button
                        type="button"
                        className={cx(ghostButtonClass, 'h-6 normal-case tracking-normal')}
                        onClick={() => openEvidence(c.event_id!)}
                      >
                        <LineIcon name="evidence" size={12} />
                        {c.label}
                      </button>
                    ) : (
                      <span className="inline-flex h-6 items-center gap-1.5 rounded-sm border border-line bg-surface-2 px-2 text-2xs text-fg-muted">
                        {c.label}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="mt-2 text-2xs text-fg-subtle">
              The engine returned no citations for this query.
            </p>
          )}
          <div className="mt-2">
            <Disclaimer>{result.synthesis.disclaimer}</Disclaimer>
          </div>
        </Disclosure>
      ) : null}

    </div>
  )
}

export default Search
