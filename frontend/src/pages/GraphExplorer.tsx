import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Badge,
  Callout,
  DataTable,
  Disclosure,
  EmptyState,
  KeyStat,
  LineIcon,
  ScreenHeader,
  SectionCard,
  SegmentedControl,
  StatRow,
  controlClass,
  cx,
  ghostButtonClass,
} from '@/components/ui'
import { GraphCanvas, NODE_FILL } from '@/components/visuals/GraphCanvas'
import { useAsyncData } from '@/lib/useAsyncData'
import { useApp } from '@/store/useApp'
import { fmtMeters, fmtNumber, fmtPage, humanizeEnum } from '@/lib/format'
import { LABEL, ResourceState, severityFillVar } from './_shared'
import {
  GraphApi,
  type GraphNode,
  type GraphRetrieve,
  type GraphRetrieveResult,
} from './_engines'

/**
 * Screen 11 — Knowledge graph explorer
 *
 * The hero is the neighbourhood: what is connected to the selected node, laid
 * out by hop distance. The secondary panel is the retrieval call that shows why
 * GraphRAG is worth having — a snippet arrives with the chain of edges that
 * reached it, so the answer is auditable rather than asserted.
 *
 * Graph statistics are the graph's census, not an answer to any question, so
 * they stay closed. A truncation notice is always shown when the server caps the
 * neighbourhood: a silently clipped graph would look complete and be wrong.
 */

const DEFAULT_ROOT = 'WELL:15/9-F-9A'
const MAX_HOPS = 3
const NODE_LIMIT = 60
const DEFAULT_QUERY = 'stuck pipe near 619 m'

type Depth = '1' | '2' | '3'

const PRESET_ROOTS = [
  { id: DEFAULT_ROOT, label: 'Current well', hint: 'WELL node for the active well' },
  { id: 'HAZARD:STUCK_PIPE', label: 'Hazard', hint: 'Every recorded stuck-pipe event' },
  {
    id: 'FORMATION:UTSIRA_FM_TOP',
    label: 'Formation',
    hint: 'Wells drilled through Utsira Fm. Top',
  },
  { id: 'SNIPPET:EVX-0202-01', label: 'Snippet', hint: 'One extracted DDR passage' },
]

/** Node label lines shown in the inspector, by declared type. */
function nodeFacts(node: GraphNode): { label: string; value: string }[] {
  const text = (key: string): string | null => {
    const value = node[key]
    return typeof value === 'string' || typeof value === 'number' ? String(value) : null
  }
  const num = (key: string): string | null => {
    const value = node[key]
    return typeof value === 'number' ? fmtNumber(value, 0) : null
  }

  const facts: { label: string; value: string }[] = [{ label: 'Node id', value: node.id }]
  switch (node.type) {
    case 'Well':
      return [
        ...facts,
        { label: 'Field', value: text('field') ?? '—' },
        { label: 'Operator', value: text('operator') ?? '—' },
        { label: 'Status', value: text('status') ?? '—' },
        { label: 'Water depth', value: num('water_depth_m') ? `${num('water_depth_m')} m` : '—' },
        { label: 'Current depth', value: num('current_depth_md') ? `${num('current_depth_md')} m MD` : '—' },
        { label: 'Total depth', value: num('total_depth_md') ? `${num('total_depth_md')} m MD` : '—' },
      ]
    case 'Event':
      return [
        ...facts,
        { label: 'Type', value: text('event_type') ?? '—' },
        { label: 'MD', value: num('md') ? `${num('md')} m` : '—' },
        { label: 'TVD', value: num('tvd') ? `${num('tvd')} m` : '—' },
        { label: 'Severity', value: text('severity') ?? '—' },
        { label: 'Formation', value: text('formation_code') ?? '—' },
        { label: 'Source document', value: text('document_id') ?? '—' },
      ]
    case 'Formation':
      return [
        ...facts,
        { label: 'Code', value: text('formation_code') ?? '—' },
        { label: 'Top', value: num('top_depth') ? `${num('top_depth')} m` : '—' },
        { label: 'Base', value: num('bottom_depth') ? `${num('bottom_depth')} m` : '—' },
        { label: 'Lithology', value: text('lithology') ?? '—' },
      ]
    case 'Hazard':
      return [
        ...facts,
        { label: 'Event type', value: text('event_type') ?? '—' },
        { label: 'Events', value: num('event_count') ?? '—' },
        { label: 'Wells', value: num('well_count') ?? '—' },
        { label: 'Example event', value: text('example_event_id') ?? '—' },
      ]
    case 'ReportSnippet':
      return [
        ...facts,
        { label: 'Document', value: text('document_id') ?? '—' },
        { label: 'Page', value: fmtPage(node.page as number | null) },
        { label: 'Extraction', value: text('extraction_method') ?? '—' },
        { label: 'Confidence', value: text('confidence') ?? '—' },
      ]
    case 'Intervention':
      return [
        ...facts,
        { label: 'Kind', value: text('intervention_type') ?? text('kind') ?? '—' },
        { label: 'Text', value: text('text') ?? '—' },
      ]
    default:
      return [
        ...facts,
        { label: 'Label', value: node.label },
      ]
  }
}

function ResultCard({ result }: { result: GraphRetrieveResult }) {
  const [open, setOpen] = useState(false)
  return (
    <li className="min-w-0 border-b border-line last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-surface-2"
      >
        <LineIcon
          name="chevron"
          size={12}
          className={cx('mt-1 shrink-0 text-fg-muted transition-transform', open && 'rotate-90')}
        />
        <span className="min-w-0 flex-1">
          {/* The hop path is the point of GraphRAG, so it leads: every node on
              the path, with the relation that carried the walk between them. */}
          <span className="flex flex-wrap items-center gap-x-1 gap-y-0.5">
            {result.path.map((nodeId, index) => (
              <span key={nodeId} className="flex items-center gap-1">
                {index > 0 ? (
                  <span className="text-2xs text-fg-subtle">
                    <span className="tnum">{index}</span> ×{' '}
                    {humanizeEnum(result.path_steps[index - 1]?.type ?? 'edge')}
                  </span>
                ) : null}
                <span className="rounded-sm bg-accent-soft px-1.5 py-0.5 text-2xs font-medium text-accent">
                  {result.path_labels[index] ?? nodeId}
                </span>
              </span>
            ))}
          </span>
          <span className="mt-1 block text-2xs leading-relaxed text-fg-muted">
            “{result.snippet.text}”
          </span>
        </span>
        <span className="tnum shrink-0 text-2xs text-fg-subtle">{result.hops} hops</span>
      </button>
      {open ? (
        <div className="border-t border-line bg-surface-2 px-3 py-2">
          <dl className="grid gap-x-4 gap-y-1 text-2xs sm:grid-cols-2">
            <div className="flex justify-between gap-2">
              <dt className="text-fg-muted">Evidence</dt>
              <dd className="tnum text-fg-strong">{result.snippet.evidence_id}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-fg-muted">Page</dt>
              <dd className="text-fg-strong">{fmtPage(result.snippet.page)}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-fg-muted">Event</dt>
              <dd className="text-fg-strong">
                {result.event.event_type} @ {fmtMeters(result.event.md, 0)} MD
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-fg-muted">Well</dt>
              <dd className="text-fg-strong">{result.well?.name ?? '—'}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-fg-muted">Extraction</dt>
              <dd className="text-fg-strong">
                {result.snippet.extraction_method.replace(/_/g, ' ').toLowerCase()} ·{' '}
                {fmtNumber(result.snippet.confidence, 2)} confidence
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-fg-muted">Provenance</dt>
              <dd className="text-fg-strong">{result.provenance.replace(/_/g, ' ').toLowerCase()}</dd>
            </div>
          </dl>
          {result.formation ? (
            <p className="mt-1 text-2xs text-fg-muted">
              Formation {result.formation.name} ({result.formation.code}),{' '}
              {fmtMeters(result.formation.top_depth)}–{fmtMeters(result.formation.bottom_depth)}.
            </p>
          ) : null}
        </div>
      ) : null}
    </li>
  )
}

export function GraphExplorer() {
  const { meta, currentWellId, openEvidence } = useApp()
  const [root, setRoot] = useState(DEFAULT_ROOT)
  const [rootInput, setRootInput] = useState(DEFAULT_ROOT)
  const [depth, setDepth] = useState<Depth>('2')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [query, setQuery] = useState(DEFAULT_QUERY)
  const [hops, setHops] = useState(MAX_HOPS)

  const subgraph = useAsyncData(
    (signal) =>
      GraphApi.subgraph(
        { root, depth: Number(depth), limit: NODE_LIMIT },
        signal,
      ),
    [root, depth],
  )
  const stats = useAsyncData((signal) => GraphApi.stats(signal), [])

  const [retrieval, setRetrieval] = useState<GraphRetrieve | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)

  const search = useCallback(async () => {
    const trimmed = query.trim()
    if (!trimmed) return
    setSearching(true)
    setSearchError(null)
    try {
      setRetrieval(await GraphApi.retrieve({ query: trimmed, root_id: root, max_hops: hops }))
    } catch (cause) {
      setRetrieval(null)
      setSearchError(cause instanceof Error ? cause.message : 'Retrieval failed')
    } finally {
      setSearching(false)
    }
  }, [query, root, hops])

  /* A new root invalidates the previous selection. */
  useEffect(() => setSelectedId(null), [root, depth])

  const nodes = subgraph.data?.nodes ?? []
  const edges = subgraph.data?.edges ?? []
  const selected = nodes.find((node) => node.id === selectedId) ?? null
  const nodeTypeCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const node of nodes) counts.set(node.type, (counts.get(node.type) ?? 0) + 1)
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [nodes])

  const statsData = stats.data

  return (
    <div className="flex flex-col gap-4">
      <ScreenHeader
        title="Knowledge graph explorer"
        subtitle="Wells, formations, events, hazards, mitigations and the DDR passages they were extracted from, in one graph. Retrieval returns the chain of edges that reached each snippet, so every answer is auditable."
        meta={
          <>
            <Badge tone="warning">{meta?.dataset_label ?? 'Real public data'}</Badge>
            {statsData ? (
              <Badge tone="info">
                {fmtNumber(statsData.node_total, 0)} nodes ·{' '}
                {fmtNumber(statsData.edge_total, 0)} edges
              </Badge>
            ) : null}
          </>
        }
      />

      {/* One filter bar for the whole screen: which node, how far, and the query. */}
      <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
        <label className="min-w-[240px]">
          <span className={LABEL}>Root node</span>
          <div className="mt-1 flex gap-1.5">
            <input
              value={rootInput}
              onChange={(event) => setRootInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') setRoot(rootInput.trim())
              }}
              placeholder="WELL:15/9-F-9A"
              className={cx(controlClass, 'w-full font-mono text-fg-strong')}
            />
            <button
              type="button"
              onClick={() => setRoot(rootInput.trim())}
              className={ghostButtonClass}
            >
              Go
            </button>
          </div>
        </label>

        <div>
          <span className={LABEL}>Neighbourhood</span>
          <div className="mt-1">
            <SegmentedControl<Depth>
              label="Hop depth"
              size="sm"
              value={depth}
              onChange={setDepth}
              options={[
                { id: '1', label: '1 hop', hint: 'Directly connected nodes only' },
                { id: '2', label: '2 hops', hint: 'One step beyond the direct neighbours' },
                { id: '3', label: '3 hops', hint: 'Wide context, heavily truncated' },
              ]}
            />
          </div>
        </div>

        <ul className="flex flex-wrap gap-1.5 pb-1">
          {PRESET_ROOTS.map((preset) => (
            <li key={preset.id}>
              <button
                type="button"
                title={preset.hint}
                onClick={() => {
                  setRoot(preset.id)
                  setRootInput(preset.id)
                }}
                className={cx(
                  'inline-flex h-6 items-center rounded-sm border px-2 text-2xs font-medium',
                  root === preset.id
                    ? 'border-accent-line bg-accent-soft text-accent'
                    : 'border-line bg-surface-1 text-fg-muted hover:border-line-strong hover:text-fg-strong',
                )}
              >
                {preset.label}
              </button>
            </li>
          ))}
        </ul>
      </div>

      {/* ------------------------------- hero ------------------------------- */}
      <ResourceState
        loading={subgraph.loading}
        error={subgraph.error}
        onRetry={subgraph.reload}
        skeleton={<div className="h-96 skeleton-block" />}
      >
        <SectionCard
          title={`Neighbourhood of ${subgraph.data?.root ?? root}`}
          description={
            subgraph.data
              ? `${subgraph.data.nodes.length} nodes and ${subgraph.data.edges.length} edges within ${subgraph.data.depth} hop${subgraph.data.depth === 1 ? '' : 's'}. Rings are hop distance from the root; colour is node type.`
              : undefined
          }
          actions={
            selected ? (
              <button
                type="button"
                onClick={() => setSelectedId(null)}
                className={ghostButtonClass}
              >
                Clear selection
              </button>
            ) : null
          }
        >
          {subgraph.data?.truncated ? (
            <div className="mb-2">
              <Callout tone="warning" title="This neighbourhood is clipped">
                The server returned {nodes.length} of a larger neighbourhood — truncated by{' '}
                {subgraph.data.truncated_by?.toLowerCase() ?? 'a server limit'} at {NODE_LIMIT}{' '}
                nodes. What is shown is a sample of the neighbourhood, not all of it.
              </Callout>
            </div>
          ) : null}

          {nodes.length <= 1 ? (
            <EmptyState
              title="No connected nodes"
              hint="This node has no neighbours within the selected hop depth. Try a deeper hop count or a different root."
            />
          ) : (
            <GraphCanvas
              nodes={nodes}
              edges={edges}
              rootId={subgraph.data?.root ?? root}
              selectedId={selectedId}
              onSelect={(node) => setSelectedId(node.id === selectedId ? null : node.id)}
            />
          )}

          {/* Selected node / neighbourhood summary — the one place numbers live. */}
          <div className="mt-3 border-t border-line pt-2">
            {selected ? (
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    aria-hidden
                    className="inline-block size-2.5 rounded-full"
                    style={{ background: NODE_FILL[selected.type] }}
                  />
                  <span className="text-sm font-semibold text-fg-strong">
                    {selected.label}
                  </span>
                  <Badge tone="neutral">{humanizeEnum(selected.type)}</Badge>
                </div>
                <dl className="mt-1.5 grid gap-x-4 gap-y-1 text-2xs sm:grid-cols-2 lg:grid-cols-3">
                  {nodeFacts(selected).map((fact) => (
                    <div key={fact.label} className="flex justify-between gap-2">
                      <dt className="truncate text-fg-muted">{fact.label}</dt>
                      <dd className="tnum shrink-0 text-fg-strong">{fact.value}</dd>
                    </div>
                  ))}
                </dl>
                {typeof selected.text === 'string' ? (
                  <p className="inset-surface mt-1.5 p-2 text-2xs leading-relaxed text-fg-muted">
                    “{selected.text}”
                  </p>
                ) : null}
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  <button
                    type="button"
                    onClick={() => setRoot(selected.id)}
                    className={ghostButtonClass}
                  >
                    Centre on this node
                  </button>
                  {typeof selected.event_id === 'string' ? (
                    <button
                      type="button"
                      onClick={() => openEvidence(selected.event_id as string)}
                      className={ghostButtonClass}
                    >
                      Open evidence
                    </button>
                  ) : null}
                </div>
              </div>
            ) : (
              <StatRow>
                <KeyStat label="Nodes shown" value={String(nodes.length)} hint={`limit ${NODE_LIMIT}`} />
                <KeyStat label="Edges shown" value={String(edges.length)} />
                <KeyStat
                  label="Node types present"
                  value={String(nodeTypeCounts.length)}
                  hint={nodeTypeCounts.map(([type, count]) => `${type} ${count}`).join(' · ')}
                />
                <KeyStat
                  label="Root type"
                  value={humanizeEnum(subgraph.data?.root_type ?? '')}
                  hint={root}
                />
              </StatRow>
            )}
          </div>
        </SectionCard>
      </ResourceState>

      {/* ---------------------------- secondary ---------------------------- */}
      <div className="grid gap-4 lg:grid-cols-5">
        <SectionCard
          className="lg:col-span-3"
          title="Graph retrieval"
          description="Snippets with the hop path that reached them — the reason to use a graph instead of a keyword index."
        >
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              void search()
            }}
          >
            <label className="min-w-[200px] flex-1">
              <span className={LABEL}>Query</span>
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="stuck pipe near 619 m"
                className={cx(controlClass, 'mt-1 w-full text-fg-strong')}
              />
            </label>
            <label>
              <span className={LABEL}>Max hops</span>
              <select
                value={hops}
                onChange={(event) => setHops(Number(event.target.value))}
                className={cx(controlClass, 'mt-1 text-fg-strong')}
              >
                {[1, 2, 3].map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" disabled={searching} className={ghostButtonClass}>
              <LineIcon name="search" size={12} />
              {searching ? 'Searching…' : 'Retrieve'}
            </button>
          </form>

          {searchError ? (
            <div className="mt-2">
              <Callout tone="critical" title="Retrieval failed">
                {searchError}
              </Callout>
            </div>
          ) : null}

          {retrieval ? (
            <>
              <p className="mt-2 text-2xs leading-relaxed text-fg-muted">
                {retrieval.parsed_intent.explain} {retrieval.result_count} snippets from{' '}
                {fmtNumber(retrieval.snippets_examined, 0)} examined, reached within {hops} hop
                {hops === 1 ? '' : 's'} of {retrieval.root} by{' '}
                {retrieval.seed_mode.replace(/_/g, ' ').toLowerCase()}.
              </p>
              <ul className="mt-1.5 max-h-[420px] overflow-y-auto">
                {retrieval.results.map((result) => (
                  <ResultCard key={result.snippet.evidence_id} result={result} />
                ))}
              </ul>
            </>
          ) : (
            <p className="mt-2 text-2xs text-fg-subtle">
              No query run yet. Retrieval is rooted at {root}; the returned path shows exactly
              which edges carried the snippet.
            </p>
          )}
        </SectionCard>

        <div className="flex flex-col gap-2 lg:col-span-2">
          <Disclosure
            summary={`Graph statistics${
              statsData
                ? ` — ${fmtNumber(statsData.node_total, 0)} nodes, ${fmtNumber(statsData.edge_total, 0)} edges`
                : ''
            }`}
            defaultOpen={false}
          >
            {statsData ? (
              <>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <p className="text-2xs font-semibold uppercase tracking-[0.06em] text-fg-muted">
                      Nodes by type
                    </p>
                    <ul className="mt-1 space-y-0.5">
                      {Object.entries(statsData.node_types)
                        .sort((a, b) => b[1] - a[1])
                        .map(([type, count]) => (
                          <li key={type} className="flex justify-between gap-2 text-2xs">
                            <span className="text-fg-muted">{humanizeEnum(type)}</span>
                            <span className="tnum text-fg-strong">{fmtNumber(count, 0)}</span>
                          </li>
                        ))}
                    </ul>
                  </div>
                  <div>
                    <p className="text-2xs font-semibold uppercase tracking-[0.06em] text-fg-muted">
                      Edges by type
                    </p>
                    <ul className="mt-1 space-y-0.5">
                      {Object.entries(statsData.edge_types)
                        .sort((a, b) => b[1] - a[1])
                        .map(([type, count]) => (
                          <li key={type} className="flex justify-between gap-2 text-2xs">
                            <span className="text-fg-muted">{humanizeEnum(type)}</span>
                            <span className="tnum text-fg-strong">{fmtNumber(count, 0)}</span>
                          </li>
                        ))}
                    </ul>
                  </div>
                </div>
                <dl className="mt-2 space-y-0.5 border-t border-line pt-2 text-2xs">
                  <div className="flex justify-between gap-2">
                    <dt className="text-fg-muted">Schema</dt>
                    <dd className="tnum text-fg-strong">{statsData.schema_version}</dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-fg-muted">Offset pairs considered</dt>
                    <dd className="tnum text-fg-strong">
                      {fmtNumber(statsData.offset_relations.considered, 0)}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-fg-muted">Offset pairs in the graph</dt>
                    <dd className="tnum text-fg-strong">
                      {fmtNumber(statsData.offset_relations.used, 0)} at min relevance{' '}
                      {fmtNumber(statsData.offset_relations.min_relevance, 2)}
                    </dd>
                  </div>
                </dl>
              </>
            ) : null}
          </Disclosure>

          <Disclosure summary="Node types in the corpus">
            <p className="mb-1.5 text-2xs leading-relaxed text-fg-muted">
              Node ids are uppercase on the wire — <span className="tnum">WELL:15/9-F-9A</span>,{' '}
              <span className="tnum">EVENT:EV-00003-000</span>,{' '}
              <span className="tnum">SNIPPET:EVX-0202-01</span> — so they read as constants in a URL
              or a log line. Type the prefix to jump straight to one.
            </p>
            <ul className="space-y-1">
              {PRESET_ROOTS.map((preset) => (
                <li key={preset.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setRoot(preset.id)
                      setRootInput(preset.id)
                    }}
                    className="flex w-full items-baseline justify-between gap-2 rounded-sm px-2 py-1 text-left text-2xs hover:bg-surface-2"
                  >
                    <span className="tnum text-fg">{preset.id}</span>
                    <span className="shrink-0 text-fg-muted">{preset.label}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Disclosure>

          <Disclosure summary="Recent events from this well (reference)">
            <WellEventsTable wellId={currentWellId} onOpen={openEvidence} />
          </Disclosure>
        </div>
      </div>
    </div>
  )
}

/** A small slice of the well's own events, for cross-checking the graph view. */
function WellEventsTable({
  wellId,
  onOpen,
}: {
  wellId: string
  onOpen: (eventId: string) => void
}) {
  const events = useAsyncData(
    (signal) => GraphApi.subgraph({ root: `WELL:${wellId}`, depth: 1, limit: 12 }, signal),
    [wellId],
  )
  const rows = (events.data?.nodes ?? []).filter((node) => node.type === 'Event')
  if (rows.length === 0) {
    return (
      <p className="text-2xs text-fg-subtle">
        {events.loading ? 'Loading…' : 'No event nodes returned for this well.'}
      </p>
    )
  }
  return (
    <>
      <p className="mb-1.5 text-2xs text-fg-muted">
        Showing {rows.length} of {events.data?.nodes.length ?? 0} nodes in the 1-hop well
        neighbourhood — a sample, not the full event history.
      </p>
      <DataTable
        rowKey={(row) => row.id}
        onRowClick={(row) => onOpen(String(row.event_id))}
        columns={[
          { key: 'label', header: 'Event' },
          {
            key: 'md',
            header: 'MD',
            align: 'right',
            width: 88,
            render: (row) => (
              <span className="tnum">
                {typeof row.md === 'number' ? fmtMeters(row.md, 0) : '—'}
              </span>
            ),
          },
          {
            key: 'severity',
            header: 'Severity',
            width: 90,
            render: (row) =>
              typeof row.severity === 'string' ? (
                <span
                  className="inline-flex items-center gap-1.5"
                  style={{ color: severityFillVar(row.severity as 'MODERATE') }}
                >
                  <span
                    aria-hidden
                    className="size-1.5 rounded-full"
                    style={{ background: severityFillVar(row.severity as 'MODERATE') }}
                  />
                  {humanizeEnum(row.severity)}
                </span>
              ) : (
                '—'
              ),
          },
        ]}
        rows={rows}
      />
    </>
  )
}
