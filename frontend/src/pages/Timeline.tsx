import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Api } from '@/api/client'
import type { EventSeverity, SeverityBand, TimelineEntry } from '@/api/types'
import {
  Badge,
  DataTable,
  DepthAxis,
  Disclosure,
  LineIcon,
  ScreenHeader,
  SectionCard,
  SegmentedControl,
  Toolbar,
  actionButtonClass,
  controlClass,
  cx,
  ghostButtonClass,
} from '@/components/ui'
import { useAsyncData } from '@/lib/useAsyncData'
import { useApp } from '@/store/useApp'
import {
  bandTone,
  fmtMeters,
  fmtNumber,
  fmtSigned,
  humanizeEnum,
  relevanceTone,
  scoreMethodLabel,
  severityTone,
} from '@/lib/format'
import {
  LABEL,
  LegendSwatch,
  ResourceState,
  clamp,
  depthPercent,
  niceStep,
  severityFillVar,
  ticksBetween,
} from './_shared'
import { ProxyTag } from './_calm'

/* ------------------------------------------------------------------ *
 * Screen 4 — Depth-aware event timeline
 *
 * One measured-depth axis on the left, one lane per well on the right, every
 * entry placed at its true MD. The plot is the hero: the point of the screen
 * is the vertical distance between "★ YOU ARE HERE" and the nearest historical
 * event above or below it, so the axis is shared rather than per-well.
 * Formation transitions are a slim band, not a lane — they are depth markers,
 * not incidents. Everything that is reference data sits in a Disclosure.
 * ------------------------------------------------------------------ */

const PLOT_H = 520
const HEADER_H = 36
const LANE_W = 268
const BAND_W = 30

const SEVERITY_RANK: Record<EventSeverity, number> = {
  LOW: 0,
  MODERATE: 1,
  HIGH: 2,
  CRITICAL: 3,
}

/** How many entries one lane paints. `null` is the deliberate "all". */
const LANE_LIMIT_CHOICES: { id: string; label: string; limit: number | null }[] = [
  { id: 'near-30', label: '30 nearest', limit: 30 },
  { id: 'near-90', label: '90 nearest', limit: 90 },
  { id: 'all', label: 'All', limit: null },
]

interface ComparisonRow {
  wellId: string
  wellName: string
  origin: string
  distanceKm: number | null
  relevance: number | null
  band: SeverityBand | null
  events: number
  eventTypes: string
  mdRange: string
  deepest: EventSeverity | null
  sharedWithCurrent: string
}

export function Timeline() {
  const { meta, currentWell, currentWellId, openEvidence } = useApp()

  const [windowMd, setWindowMd] = useState(300)
  const [eventType, setEventType] = useState('')
  const [wellFilter, setWellFilter] = useState('')
  const [tip, setTip] = useState<{ entry: TimelineEntry; x: number; y: number } | null>(null)
  const [laneLimitId, setLaneLimitId] = useState('near-30')

  const timeline = useAsyncData(
    (signal) =>
      Api.timeline(
        currentWellId,
        { window_md: windowMd, event_type: eventType || undefined },
        signal,
      ),
    [currentWellId, windowMd, eventType],
  )

  const nearby = useAsyncData(
    (signal) => Api.nearby(currentWellId, { radius_km: 12, min_relevance: 0 }, signal),
    [currentWellId],
  )

  const data = timeline.data
  const windowSpec = data?.window
  const topMd = windowSpec?.top_md ?? Math.max((currentWell?.current_depth_md ?? 0) - windowMd, 0)
  const bottomMd = windowSpec?.bottom_md ?? (currentWell?.current_depth_md ?? 0) + windowMd
  const span = Math.max(bottomMd - topMd, 1)
  const ticks = ticksBetween(topMd, bottomMd, niceStep(span / 8))
  const yPct = (depth: number) => clamp(depthPercent(depth, topMd, bottomMd), -8, 108)
  const currentMd = data?.current_marker.md ?? currentWell?.current_depth_md ?? 0

  /* Formation transitions are returned once, globally (origin FORMATIONS,
     well_id null), so they render as one slim band, not a per-well lane. */
  const transitions = useMemo(
    () =>
      (data?.entries ?? [])
        .filter((e) => e.kind === 'formation_transition')
        .sort((a, b) => a.md - b.md),
    [data],
  )

  /* Lane order: current well first, then offsets in engine relevance order. */
  const lanes = useMemo(() => {
    const byWell = new Map<string, TimelineEntry[]>()
    for (const entry of data?.entries ?? []) {
      if (!entry.well_id || entry.kind === 'formation_transition') continue
      if (entry.kind === 'current_marker') continue
      const bucket = byWell.get(entry.well_id)
      if (bucket) bucket.push(entry)
      else byWell.set(entry.well_id, [entry])
    }
    const rank = new Map((nearby.data?.items ?? []).map((o, i) => [o.well.id, i]))
    return [...byWell.entries()]
      .filter(([wellId]) => wellId !== currentWellId)
      .sort((a, b) => (rank.get(a[0]) ?? 999) - (rank.get(b[0]) ?? 999))
      .map(([wellId, entries]) => ({
        wellId,
        wellName: entries[0]?.well_name ?? wellId,
        origin: entries[0]?.origin ?? 'OFFSET',
        entries: entries.slice().sort((a, b) => a.md - b.md),
      }))
  }, [data, nearby.data, currentWellId])

  const allCurrentEntries = useMemo(
    () =>
      (data?.entries ?? [])
        .filter((e) => e.well_id === currentWellId && e.kind !== 'current_marker')
        .sort((a, b) => a.md - b.md),
    [data, currentWellId],
  )

  /* Well filter narrows the lanes on the client — the window itself still comes
     from the API, so the axis never moves under the reader. */
  const visibleLanes = useMemo(
    () => (wellFilter === '' ? lanes : lanes.filter((l) => l.wellId === wellFilter)),
    [lanes, wellFilter],
  )
  const currentVisible = wellFilter === '' || wellFilter === currentWellId
  /* A ±300 m window over the real corpus returns ~316 events on the active well
     alone. Painting them all into one 520px lane is a smear, and the screen's
     question is "how far is the nearest event from me" — so the default slice is
     the entries nearest the current MD, and the reader can widen it. */
  const perLaneLimit = LANE_LIMIT_CHOICES.find((c) => c.id === laneLimitId)?.limit ?? 30
  const nearestToBit = (entries: TimelineEntry[]) =>
    entries
      .slice()
      .sort(
        (a, b) =>
          Math.abs(a.delta_from_current_md ?? a.md - currentMd) -
          Math.abs(b.delta_from_current_md ?? b.md - currentMd),
      )
  const allCurrent = currentVisible ? allCurrentEntries : []
  const currentEntries = useMemo(
    () =>
      perLaneLimit === null ? allCurrent : nearestToBit(allCurrent).slice(0, perLaneLimit),
    [allCurrent, perLaneLimit, currentMd],
  )

  const comparison: ComparisonRow[] = useMemo(() => {
    const rows: ComparisonRow[] = []
    const offsetMeta = new Map((nearby.data?.items ?? []).map((o) => [o.well.id, o]))
    const currentTypes = new Set(
      allCurrentEntries
        .filter((e) => e.kind === 'drilling_event')
        .map((e) => e.event_type),
    )

    const build = (wellId: string, wellName: string, origin: string, entries: TimelineEntry[]) => {
      const events = entries.filter((e) => e.kind === 'drilling_event')
      const meta0 = offsetMeta.get(wellId)
      const types = [...new Set(events.map((e) => e.event_label ?? e.event_type).filter(Boolean))]
      const deepest = events.reduce<EventSeverity | null>((acc, e) => {
        if (!e.severity) return acc
        if (!acc || SEVERITY_RANK[e.severity] > SEVERITY_RANK[acc]) return e.severity
        return acc
      }, null)
      const mds = entries.map((e) => e.md)
      rows.push({
        wellId,
        wellName,
        origin,
        distanceKm: meta0 ? meta0.distance_km : null,
        relevance: meta0 ? meta0.relevance_score : null,
        band: meta0 ? meta0.relevance_band : null,
        events: events.length,
        eventTypes: types.length ? types.join(', ') : '—',
        mdRange:
          mds.length > 0
            ? `${fmtNumber(Math.min(...mds), 0)} – ${fmtNumber(Math.max(...mds), 0)} m`
            : '—',
        deepest,
        sharedWithCurrent:
          origin === 'CURRENT'
            ? '—'
            : [...new Set(events.map((e) => e.event_type))]
                .filter((t) => currentTypes.has(t))
                .map((t) => humanizeEnum(t))
                .join(', ') || 'none',
      })
    }

    build(currentWellId, allCurrentEntries[0]?.well_name ?? currentWellId, 'CURRENT', allCurrentEntries)
    for (const lane of lanes) build(lane.wellId, lane.wellName, lane.origin, lane.entries)
    return rows
  }, [allCurrentEntries, lanes, currentWellId])

  const wellOptions = useMemo(
    () => [
      { id: currentWellId, name: allCurrentEntries[0]?.well_name ?? currentWell?.name ?? currentWellId },
      ...lanes.map((l) => ({ id: l.wellId, name: l.wellName })),
    ],
    [allCurrentEntries, currentWell, currentWellId, lanes],
  )

  return (
    <div className="flex flex-col gap-4">
      <ScreenHeader
        title="Depth timeline"
        subtitle="Measured-depth view of the current well and the relevant offsets around it — every entry sits at its true MD, so the gap to ★ YOU ARE HERE is the real interval."
        meta={
          <>
            <Badge tone="warning">{meta?.dataset_label ?? 'Real public data'}</Badge>
            <Badge tone="neutral">
              {data ? `${data.entries.length} entries` : 'loading window'}
            </Badge>
          </>
        }
        actions={
          <Link to="/offset-intelligence/replay" className={ghostButtonClass}>
            <LineIcon name="replay" size={13} />
            Depth replay
          </Link>
        }
      />

      <Toolbar className="flex flex-wrap items-end gap-x-4 gap-y-3">
        <div className="w-64">
          <label className={LABEL} htmlFor="tl-window">
            Window — metres above / below current MD
          </label>
          <div className="mt-1 flex items-center gap-2">
            <input
              id="tl-window"
              type="range"
              min={100}
              max={1200}
              step={50}
              value={windowMd}
              className="h-7 w-full accent-accent"
              onChange={(e) => setWindowMd(Number(e.target.value))}
            />
            <span className="tnum w-16 shrink-0 text-right text-xs font-semibold text-fg-strong">
              ± {windowMd} m
            </span>
          </div>
        </div>

        <div className="w-52">
          <label className={LABEL} htmlFor="tl-well">
            Well
          </label>
          <select
            id="tl-well"
            className={cx(controlClass, 'mt-1 w-full')}
            value={wellFilter}
            onChange={(e) => setWellFilter(e.target.value)}
          >
            <option value="">All lanes</option>
            {wellOptions.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        </div>

        {/* Event type is a 12-value registry. As chips it is the widest thing on
            the bar and competes with the plot; as a select it is one control
            that can still show every type. */}
        <div className="w-48">
          <label className={LABEL} htmlFor="tl-type">
            Event type
          </label>
          <select
            id="tl-type"
            className={cx(controlClass, 'mt-1 w-full')}
            value={eventType}
            onChange={(e) => setEventType(e.target.value)}
          >
            <option value="">All types</option>
            {(meta?.event_types ?? []).map((t) => (
              <option key={t.code} value={t.code}>
                {t.label}
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-1.5">
          <span className={LABEL}>Entries per lane</span>
          <SegmentedControl
            label="Entries per lane"
            size="sm"
            value={laneLimitId}
            options={LANE_LIMIT_CHOICES.map((c) => ({ id: c.id, label: c.label }))}
            onChange={setLaneLimitId}
          />
        </div>

        <p className="tnum ml-auto text-2xs text-fg-muted">
          {`Window ${fmtMeters(topMd)} – ${fmtMeters(bottomMd)} MD · ${allCurrentEntries.length} entries on the current well`}
        </p>
      </Toolbar>

      <SectionCard
        title={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span>Depth lanes</span>
            <span aria-hidden className="text-fg-subtle">
              ·
            </span>
            <span className="font-medium text-fg-muted">
              {data?.current_well.name ?? currentWellId}
            </span>
            <ProxyTag>15/9 proxy formation</ProxyTag>
          </span>
        }
        description={`At ${fmtMeters(currentMd)} MD · ${fmtMeters(data?.current_marker.tvd)} TVD · ${data?.current_marker.formation ?? '—'}`}
        dense
        className="plot-surface"
      >
        <ResourceState
          loading={timeline.loading && !timeline.data}
          error={timeline.error}
          onRetry={timeline.reload}
          skeleton={<div className="skeleton-block h-[560px] w-full rounded-md" />}
        >
          {data ? (
            <div className="scroll-thin overflow-x-auto">
              <div className="flex">
                <div className="shrink-0">
                  <div
                    className="flex items-end justify-end border-b border-line bg-plot-bg px-1 pb-1 text-2xs uppercase tracking-wider text-fg-subtle"
                    style={{ height: HEADER_H, width: 56 }}
                  >
                    MD m
                  </div>
                  <DepthAxis
                    topM={topMd}
                    bottomM={bottomMd}
                    labels={ticks}
                    className="h-[520px] bg-plot-bg"
                  />
                </div>

                <div className="relative">
                  <div
                    className="pointer-events-none absolute inset-x-0 bottom-0 top-0 z-0"
                    style={{ marginTop: HEADER_H }}
                  >
                    {ticks.map((t) => (
                      <div
                        key={t}
                        className="absolute inset-x-0 border-t"
                        style={{ top: `${yPct(t)}%`, borderColor: 'var(--grid)' }}
                      />
                    ))}
                    <div
                      className="absolute inset-x-0 border-t-2 border-dashed"
                      style={{ top: `${yPct(currentMd)}%`, borderColor: 'var(--warn)' }}
                    />
                  </div>

                  <div className="relative flex">
                    {/* Formation transitions — a slim band, not a lane. */}
                    <div
                      className="z-10 shrink-0 border-r border-line bg-surface-2"
                      style={{ width: BAND_W }}
                    >
                      <LaneHeader title="Form" subtitle={`${transitions.length}`} />
                      <div className="relative" style={{ height: PLOT_H }}>
                        <div
                          className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2"
                          style={{ backgroundColor: 'var(--accent-line)' }}
                        />
                        {transitions.map((t) => (
                          <div
                            key={t.id}
                            className="absolute inset-x-0 -translate-y-1/2"
                            style={{ top: `${yPct(t.md)}%` }}
                          >
                            <div
                              className="mx-auto h-2.5 w-2.5 border"
                              style={{
                                backgroundColor: 'var(--accent-soft)',
                                borderColor: 'var(--accent-line)',
                              }}
                            />
                          </div>
                        ))}
                      </div>
                    </div>

                    {currentVisible ? (
                      <div
                        className="z-10 shrink-0 border-r border-line bg-plot-bg"
                        style={{ width: LANE_W }}
                      >
                        <LaneHeader
                          title={allCurrentEntries[0]?.well_name ?? currentWellId}
                          subtitle={
                            currentEntries.length < allCurrent.length
                              ? `${currentEntries.length} of ${allCurrent.length} in window`
                              : 'current well'
                          }
                          current
                        />
                        <div className="relative" style={{ height: PLOT_H }}>
                          <div
                            className="absolute inset-x-1 z-30 -translate-y-1/2"
                            style={{ top: `${yPct(currentMd)}%` }}
                          >
                            <div className="inline-flex items-center gap-1.5 rounded-sm bg-warn-soft px-2 py-1 text-2xs font-semibold text-warn ring-1 ring-warn-line">
                              <LineIcon name="compass" size={12} />★ YOU ARE HERE
                            </div>
                          </div>
                          {currentEntries.map((entry) => (
                            <EntryCard
                              key={entry.id}
                              entry={entry}
                              topPct={yPct(entry.md)}
                              onHover={setTip}
                              onOpen={() => openEvidence(entry.id)}
                            />
                          ))}
                          {currentEntries.length === 0 ? (
                            <p className="px-2 pt-2 text-2xs text-fg-subtle">
                              No events recorded in this window.
                            </p>
                          ) : null}
                        </div>
                      </div>
                    ) : null}

                    {visibleLanes.length === 0 ? (
                      <div
                        className="flex items-center px-6 text-xs text-fg-subtle"
                        style={{ height: PLOT_H }}
                      >
                        {wellFilter === ''
                          ? 'No offset well events in this window.'
                          : 'This well has no events inside the window.'}
                      </div>
                    ) : (
                      visibleLanes.map((lane) => {
                        const shown =
                          perLaneLimit === null
                            ? lane.entries
                            : nearestToBit(lane.entries).slice(0, perLaneLimit)
                        return (
                          <div
                            key={lane.wellId}
                            className="relative z-10 shrink-0 border-r border-line bg-plot-bg"
                            style={{ width: LANE_W }}
                          >
                            <LaneHeader
                              title={lane.wellName}
                              subtitle={
                                shown.length < lane.entries.length
                                  ? `${shown.length} of ${lane.entries.length} in window`
                                  : 'offset well'
                              }
                            />
                            <div className="relative" style={{ height: PLOT_H }}>
                              {shown.map((entry) => (
                                <EntryCard
                                  key={entry.id}
                                  entry={entry}
                                  topPct={yPct(entry.md)}
                                  onHover={setTip}
                                  onOpen={() => openEvidence(entry.id)}
                                />
                              ))}
                            </div>
                          </div>
                        )
                      })
                    )}
                  </div>
                </div>
              </div>
            </div>
          ) : null}
        </ResourceState>

        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line pt-3">
          <LegendSwatch
            className="border-2"
            color="var(--warn-soft)"
            label="Current position"
          />
          <LegendSwatch color="var(--band-critical)" label="Critical" />
          <LegendSwatch color="var(--band-high)" label="High" />
          <LegendSwatch color="var(--band-warning)" label="Moderate" />
          <LegendSwatch color="var(--ok)" label="Low" />
          <LegendSwatch className="border" color="var(--accent-soft)" label="Formation transition" />
          <span className="ml-auto text-2xs text-fg-subtle">
            Click an entry to open its evidence chain
          </span>
        </div>
      </SectionCard>

      <Disclosure
        summary={`Current well vs offset wells — ${comparison.length} lanes in this window`}
        badge={<Badge tone="muted">reference</Badge>}
      >
        <DataTable
          rows={comparison}
          rowKey={(r) => r.wellId}
          selectedKey={currentWellId}
          columns={[
            {
              key: 'well',
              header: 'Well',
              render: (r) => (
                <span className="flex items-center gap-1.5">
                  <LineIcon
                    name={r.origin === 'CURRENT' ? 'well' : 'offset'}
                    size={12}
                    title={r.origin === 'CURRENT' ? 'Current well' : 'Offset well'}
                  />
                  <span className="font-medium text-fg-strong">{r.wellName}</span>
                </span>
              ),
            },
            {
              key: 'origin',
              header: 'Origin',
              render: (r) => <Badge tone={r.origin === 'CURRENT' ? 'info' : 'muted'}>{r.origin}</Badge>,
            },
            {
              key: 'distance',
              header: 'Distance',
              align: 'right',
              render: (r) => (
                <span className="tnum">
                  {r.distanceKm === null ? '—' : `${r.distanceKm.toFixed(1)} km`}
                </span>
              ),
            },
            {
              key: 'relevance',
              header: 'Relevance',
              align: 'right',
              render: (r) => (
                <span className={cx('tnum', r.relevance !== null && relevanceTone(r.relevance))}>
                  {r.relevance === null ? '—' : r.relevance.toFixed(2)}
                </span>
              ),
            },
            {
              key: 'band',
              header: 'Band',
              align: 'right',
              render: (r) =>
                r.band ? (
                  <Badge tone={bandTone(r.band)}>{r.band}</Badge>
                ) : (
                  <span className="text-2xs text-fg-subtle">current well</span>
                ),
            },
            {
              key: 'events',
              header: 'Events',
              align: 'right',
              render: (r) => <span className="tnum font-semibold">{r.events}</span>,
            },
            {
              key: 'eventTypes',
              header: 'Event types',
              render: (r) => <span className="text-2xs text-fg-muted">{r.eventTypes}</span>,
            },
            {
              key: 'mdRange',
              header: 'MD range',
              align: 'right',
              render: (r) => <span className="tnum">{r.mdRange}</span>,
            },
            {
              key: 'deepest',
              header: 'Deepest severity',
              align: 'right',
              render: (r) =>
                r.deepest ? (
                  <Badge tone={severityTone(r.deepest)}>{r.deepest}</Badge>
                ) : (
                  <span className="text-2xs text-fg-subtle">—</span>
                ),
            },
            {
              key: 'shared',
              header: 'Also in current well',
              render: (r) => <span className="text-2xs text-fg-muted">{r.sharedWithCurrent}</span>,
            },
          ]}
        />
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2">
          <p className="text-2xs text-fg-subtle">
            Distance and relevance come from GET /api/v1/wells/&#123;id&#125;/nearby; the window
            itself comes from GET /api/v1/wells/&#123;id&#125;/timeline. Nothing is recomputed
            here.
          </p>
          <Link to="/alerts" className={actionButtonClass}>
            <LineIcon name="alert" size={12} />
            Alert centre
          </Link>
        </div>
      </Disclosure>

      <Disclosure summary="How to read this plot">
        <ul className="flex flex-col gap-1.5 text-2xs leading-relaxed text-fg-muted">
          <li>
            The left ruler is one shared measured-depth axis. A card&apos;s height <em>is</em> the
            depth interval to ★ YOU ARE HERE — nothing is offset for layout.
          </li>
          <li>
            Formation-transition markers (the slim band) are depth markers the engine derives from
            the formation column. They are not incidents and carry no evidence.
          </li>
          <li>
            Distances shown on each card are the engine&apos;s delta from the current measured depth.
            Severity colours come from the event record; red is reserved for critical.
          </li>
          <li>
            Method score {scoreMethodLabel(data?.method)} · provenance {humanizeEnum(data?.data_provenance)}.
          </li>
        </ul>
      </Disclosure>

      {tip ? (
        <div
          className="panel-surface pointer-events-none fixed z-50 w-80 p-3"
          style={{ left: Math.min(tip.x + 16, window.innerWidth - 340), top: tip.y + 16 }}
          role="tooltip"
        >
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-semibold text-fg-strong">
              {tip.entry.event_label ?? humanizeEnum(tip.entry.event_type)}
            </p>
            {tip.entry.severity ? (
              <Badge tone={severityTone(tip.entry.severity)}>{tip.entry.severity}</Badge>
            ) : null}
          </div>
          <p className="tnum mt-0.5 text-2xs text-fg-muted">
            {`${tip.entry.id} · ${tip.entry.well_name} · MD ${fmtMeters(tip.entry.md)} · TVD ${fmtMeters(tip.entry.tvd)}`}
          </p>
          <p className="mt-1 text-2xs text-fg-muted">
            {`${tip.entry.formation ?? '—'} · ${tip.entry.origin} · ${tip.entry.evidence_count} evidence record(s)`}
          </p>
          {tip.entry.relevance_note ? (
            <p className="mt-1.5 text-2xs text-fg-muted">{tip.entry.relevance_note}</p>
          ) : null}
          {tip.entry.description ? (
            <p className="mt-1.5 text-2xs leading-relaxed text-fg-muted">{tip.entry.description}</p>
          ) : null}
          {tip.entry.mitigation ? (
            <p className="mt-1.5 border-t border-line pt-1.5 text-2xs leading-relaxed text-fg-muted">
              <span className="font-semibold text-fg">Mitigation: </span>
              {tip.entry.mitigation}
            </p>
          ) : null}
          <p className="mt-1.5 text-2xs text-fg-subtle">
            {tip.entry.document_id
              ? `Source document ${tip.entry.document_id}`
              : 'No source document attached'}
          </p>
        </div>
      ) : null}
    </div>
  )
}

function LaneHeader({ title, subtitle, current }: { title: string; subtitle: string; current?: boolean }) {
  return (
    <div
      className={cx(
        'flex flex-col justify-center border-b border-line px-2',
        current ? 'bg-chrome text-chrome-fg' : 'bg-surface-2 text-fg-muted',
      )}
      style={{ height: HEADER_H }}
    >
      <span className="truncate text-2xs font-semibold uppercase tracking-wider">{title}</span>
      <span className="truncate text-2xs opacity-75">{subtitle}</span>
    </div>
  )
}

function EntryCard({
  entry,
  topPct,
  onHover,
  onOpen,
}: {
  entry: TimelineEntry
  topPct: number
  onHover: (tip: { entry: TimelineEntry; x: number; y: number } | null) => void
  onOpen: () => void
}) {
  const isMitigation = entry.kind === 'mitigation'

  return (
    <button
      type="button"
      className={cx(
        'absolute inset-x-1 z-10 flex -translate-y-1/2 items-stretch gap-1.5 rounded-sm border px-1.5 py-1 text-left transition-colors hover:bg-surface-2',
        isMitigation ? 'border-ok-line bg-ok-soft' : 'border-line bg-surface-1',
      )}
      style={{ top: `${topPct}%` }}
      onMouseEnter={(e) => onHover({ entry, x: e.clientX, y: e.clientY })}
      onMouseMove={(e) => onHover({ entry, x: e.clientX, y: e.clientY })}
      onMouseLeave={() => onHover(null)}
      onFocus={(e) =>
        onHover({
          entry,
          x: e.currentTarget.getBoundingClientRect().left,
          y: e.currentTarget.getBoundingClientRect().bottom,
        })
      }
      onBlur={() => onHover(null)}
      onClick={onOpen}
    >
      <span
        className="w-1 shrink-0 rounded-sm"
        style={{
          backgroundColor: isMitigation ? 'var(--ok)' : severityFillVar(entry.severity),
        }}
      />
      <span className="min-w-0 flex-1">
        <span className="flex items-center justify-between gap-1">
          <span className="truncate text-2xs font-semibold text-fg-strong">
            {isMitigation ? 'Mitigation' : (entry.event_label ?? humanizeEnum(entry.event_type))}
          </span>
          {entry.severity && !isMitigation ? (
            <span className="shrink-0 text-2xs font-semibold text-fg-muted">{entry.severity}</span>
          ) : null}
        </span>
        <span className="tnum block truncate text-2xs text-fg-muted">
          {`MD ${fmtNumber(entry.md, 0)} m · TVD ${fmtNumber(entry.tvd, 0)} m · ${entry.formation || '—'}`}
        </span>
        <span className="flex items-center justify-between gap-1 text-2xs text-fg-subtle">
          <span className="tnum">
            {entry.delta_from_current_md === null
              ? '—'
              : `${fmtSigned(entry.delta_from_current_md, 0)} m vs current`}
          </span>
          <span className="tnum truncate">{entry.document_id ?? 'no document'}</span>
        </span>
        {entry.mitigation ? (
          <span className="mt-0.5 line-clamp-2 block text-2xs leading-snug text-fg-muted">
            {entry.mitigation}
          </span>
        ) : null}
      </span>
    </button>
  )
}

export default Timeline
