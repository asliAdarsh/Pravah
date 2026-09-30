import { useMemo, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Api } from '@/api/client'
import type { EventDetail, ReplayOffsetWell } from '@/api/types'
import {
  Badge,
  DataTable,
  DepthAxis,
  Disclosure,
  EmptyState,
  LineIcon,
  Meter,
  SectionCard,
  SegmentedControl,
  controlClass,
  cx,
  ghostButtonClass,
  type Tone,
} from '@/components/ui'
import { useAsyncData } from '@/lib/useAsyncData'
import { useApp } from '@/store/useApp'
import {
  bandDotClass,
  bandTone,
  fmtDate,
  fmtMeters,
  fmtNumber,
  fmtPercent,
  humanizeEnum,
  scoreMethodLabel,
  severityTone,
} from '@/lib/format'
import {
  LABEL,
  LegendSwatch,
  ResourceState,
  ScreenHeader,
  clamp,
  depthPercent,
  niceStep,
  severityFillVar,
  ticksBetween,
} from './_shared'
import {
  AlertEvidence,
  FormationName,
  ProxyTag,
  isProxyFormation,
  isTelemetryAlert,
} from './_calm'

/* ------------------------------------------------------------------ *
 * Screen 3 — Offset Replay (the signature view)
 *
 * Anatomy (UX contract §4.3): the shared TVD scale is the HERO, full width and
 * tall, and it keeps its three headline values in the plot header. Everything
 * that drives it — depth window, event type, offset selection, recurring
 * hazards, alerts — moves into one right rail, and the secondary drilling
 * parameters collapse into a Disclosure.
 *
 * Columns share ONE true-vertical-scale so "where I am now" can be read
 * against "what happened there before". Every mark is a plain div/SVG
 * primitive: no chart library, so the view stays offline-safe and sharp at
 * any zoom.
 * ------------------------------------------------------------------ */

const PLOT_H = 520
const HEADER_H = 36
const CURRENT_COL = 208
const OFFSET_COL = 186
const FORMATION_COL = 168
const STICKY_COL = 12

const ALIGNMENT_TONE: Record<string, Tone> = {
  SAME: 'success',
  ADJACENT: 'warning',
  DIFFERENT: 'muted',
  UNKNOWN: 'muted',
}

const RADIUS_CHOICES = [4, 6, 8, 12, 20]

/** How many event chips one column paints. `null` is the deliberate "all". */
const CHIP_CAP_CHOICES: { id: string; label: string; limit: number | null }[] = [
  { id: 'near-40', label: '40 nearest', limit: 40 },
  { id: 'near-120', label: '120 nearest', limit: 120 },
  { id: 'all', label: 'All in window', limit: null },
]

interface Tip {
  event: EventDetail
  x: number
  y: number
}

export function OffsetReplay() {
  const { meta, currentWell, currentWellId, openEvidence, openDecision } = useApp()
  const [searchParams, setSearchParams] = useSearchParams()

  const [radiusKm, setRadiusKm] = useState(8)
  const [eventType, setEventType] = useState('')
  const [topOverride, setTopOverride] = useState<number | null>(null)
  const [bottomOverride, setBottomOverride] = useState<number | null>(null)
  const [tip, setTip] = useState<Tip | null>(null)
  const [chipCapId, setChipCapId] = useState('near-40')

  const replay = useAsyncData(
    (signal) =>
      Api.replay(
        currentWellId,
        {
          radius_km: radiusKm,
          event_type: eventType || undefined,
          top_tvd: topOverride ?? undefined,
          bottom_tvd: bottomOverride ?? undefined,
          limit_wells: 8,
        },
        signal,
      ),
    [currentWellId, radiusKm, eventType, topOverride, bottomOverride],
  )

  const data = replay.data
  const currentTvd = data?.current_well.current_tvd ?? 0
  const topTvd = topOverride ?? data?.window.top_tvd ?? Math.max(currentTvd - 300, 0)
  const bottomTvd = bottomOverride ?? data?.window.bottom_tvd ?? currentTvd + 300
  const span = Math.max(bottomTvd - topTvd, 1)
  const tickStep = niceStep(span / 8)
  const ticks = ticksBetween(topTvd, bottomTvd, tickStep)

  const offsetWells = useMemo(() => data?.offset_wells ?? [], [data])

  /* `?offsets=` drives which columns are plotted; with no deep link the top
     four by relevance are shown. */
  const selectedIds = useMemo(() => {
    const valid = new Set(offsetWells.map((o) => o.well.id))
    const requested = (searchParams.get('offsets') ?? '')
      .split(',')
      .map((s) => s.trim())
      .filter((id) => valid.has(id))
    if (requested.length > 0) return requested
    return offsetWells
      .slice()
      .sort((a, b) => b.relevance_score - a.relevance_score)
      .slice(0, 4)
      .map((o) => o.well.id)
  }, [offsetWells, searchParams])

  const shownWells = offsetWells.filter((o) => selectedIds.includes(o.well.id))
  const yPct = (depth: number) => clamp(depthPercent(depth, topTvd, bottomTvd), -8, 108)

  const toggleOffset = (id: string) => {
    const next = new Set(selectedIds)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    const params = new URLSearchParams(searchParams)
    if (next.size === 0) params.delete('offsets')
    else params.set('offsets', [...next].join(','))
    setSearchParams(params, { replace: true })
  }

  const zoomToEvents = () => {
    const depths = [
      ...(data?.current_events ?? []).map((e) => e.tvd),
      ...shownWells.flatMap((o) => o.events.map((e) => e.tvd)),
    ].filter((d) => Number.isFinite(d))
    if (depths.length === 0) return
    const lo = Math.min(...depths)
    const hi = Math.max(...depths)
    const pad = Math.max(30, (hi - lo) * 0.15)
    setTopOverride(Math.floor(lo - pad))
    setBottomOverride(Math.ceil(hi + pad))
  }

  const snapToCurrent = () => {
    setTopOverride(Math.round(currentTvd - 150))
    setBottomOverride(Math.round(currentTvd + 150))
  }

  const step = (which: 'top' | 'bottom', delta: number) => {
    const setter = which === 'top' ? setTopOverride : setBottomOverride
    setter((prev) => Math.round((prev ?? (which === 'top' ? topTvd : bottomTvd)) + delta))
  }

  const eventCount =
    (data?.current_events.length ?? 0) + shownWells.reduce((n, o) => n + o.events.length, 0)

  /* The current well's window holds ~316 extracted events. Painting all of
     them into a 520px column turns the hero into an unreadable smear, and the
     screen's question is "what happened at MY depth" — so the default slice is
     the events nearest the current TVD, and the rail can widen it. */
  const chipCap = CHIP_CAP_CHOICES.find((c) => c.id === chipCapId)?.limit ?? 40
  const currentEvents = useMemo(() => {
    const all = data?.current_events ?? []
    if (chipCap === null) return all
    return all
      .slice()
      .sort((a, b) => Math.abs(a.tvd - currentTvd) - Math.abs(b.tvd - currentTvd))
      .slice(0, chipCap)
  }, [data, chipCap, currentTvd])
  const currentEventsTotal = data?.current_events.length ?? 0

  /* 15/9-F-9A has no published formation rows, so its whole column is a
     labelled 15/9-block proxy. The summary shape inside `/offset-replay` carries
     no provenance note, so the flag comes from the full well record. */
  const centreFormationIsProxy = isProxyFormation(currentWell)

  const eventTypeOptions = [
    { id: '', label: 'All types' },
    ...(meta?.event_types ?? []).map((t) => ({ id: t.code, label: t.label })),
  ]

  return (
    <div className="flex flex-col gap-3 p-3">
      <ScreenHeader
        title="Offset replay"
        subtitle="Your current position against what offset wells recorded at the same depth."
        actions={
          <Link to="/offset-intelligence/map" className={ghostButtonClass}>
            <LineIcon name="map" size={13} />
            Location plan
          </Link>
        }
      />

      {/* ==================== HERO — the shared TVD scale ==================== */}
      <SectionCard
        title="Shared TVD scale"
        description={`${data?.current_well.name ?? currentWellId} against ${shownWells.length} selected offset${shownWells.length === 1 ? '' : 's'} · read across a row to see who recorded an event at your current depth`}
        actions={
          <>
            <PlotHeaderStat label="Current TVD" value={fmtMeters(currentTvd)} tone="warning" />
            <PlotHeaderStat
              label="Formation"
              value={<FormationName well={currentWell} name={data?.current_well.current_formation?.name} />}
            />
            <PlotHeaderStat
              label="Hole size"
              value={currentWell?.operating_context?.bit_size ?? '—'}
            />
          </>
        }
        dense
      >
        <ResourceState
          loading={replay.loading && !replay.data}
          error={replay.error}
          onRetry={replay.reload}
          skeleton={<div className="skeleton-block h-[560px] w-full rounded-md" />}
        >
          {data ? (
            <div className="plot-surface scroll-thin overflow-x-auto p-1">
              <div className="flex">
                <div className="shrink-0 bg-[var(--plot-bg)]">
                  <div
                    className="flex items-end justify-end border-b border-line px-1 pb-1 text-2xs uppercase tracking-wider text-fg-subtle"
                    style={{ height: HEADER_H, width: 56 }}
                  >
                    TVD m
                  </div>
                  <DepthAxis topM={topTvd} bottomM={bottomTvd} labels={ticks} className="h-[520px]" />
                </div>

                <div className="relative">
                  {/* shared gridlines + the "you are here" depth rule */}
                  <div
                    className="pointer-events-none absolute inset-x-0 bottom-0 top-0 z-0"
                    style={{ marginTop: HEADER_H }}
                  >
                    {ticks.map((t) => (
                      <div
                        key={t}
                        className="absolute inset-x-0 border-t border-grid"
                        style={{ top: `${yPct(t)}%` }}
                      />
                    ))}
                    <div
                      className="absolute inset-x-0 border-t-2 border-dashed border-warn"
                      style={{ top: `${yPct(currentTvd)}%` }}
                    />
                  </div>

                  <div className="relative flex">
                    {/* column 1 — current well */}
                    <div
                      className="sticky z-20 shrink-0 border-l border-r border-line bg-[var(--plot-bg)]"
                      style={{ width: CURRENT_COL, left: STICKY_COL }}
                    >
                      <ColumnHeader
                        title={data.current_well.name}
                        subtitle={`${data.current_well.id} · drilling`}
                        tone="current"
                      />
                      <div className="relative" style={{ height: PLOT_H }}>
                        {/* z-30, above the event chips (z-10): a chip recorded
                            at the current TVD would otherwise paint straight
                            over the one marker that anchors the whole view. */}
                        <div
                          className="absolute inset-x-0 z-30 -translate-y-1/2"
                          style={{ top: `${yPct(currentTvd)}%` }}
                        >
                          {/* The one marker that must survive both themes: an
                              opaque label plate so the amber rule is never
                              text-on-text. */}
                          <div className="mx-1 flex items-center gap-1.5 rounded-sm border border-warn-line bg-warn-soft px-2 py-1 text-2xs font-semibold text-warn shadow-[0_1px_2px_rgba(0,0,0,0.25)]">
                            <LineIcon name="compass" size={12} />
                            ★ YOU ARE HERE
                            <span className="tnum font-normal">
                              {fmtMeters(currentTvd)}
                            </span>
                          </div>
                        </div>
                        {currentEvents.map((ev) => (
                          <EventChip
                            key={ev.id}
                            event={ev}
                            topPct={yPct(ev.tvd)}
                            onHover={setTip}
                            onOpen={() => openEvidence(ev.id)}
                          />
                        ))}
                        {/* The bound is stated, never silent: a reader must be
                            able to tell that a deliberate slice is showing, and
                            widen it from the rail. */}
                        {currentEvents.length < currentEventsTotal ? (
                          <div
                            className="absolute inset-x-0 bottom-0 z-20 border-t border-line bg-[var(--plot-bg)]/95 px-1.5 py-1 text-2xs text-fg-subtle"
                            style={{ top: 'auto' }}
                          >
                            <span className="tnum">
                              {`${currentEvents.length} of ${currentEventsTotal} events · nearest first`}
                            </span>
                          </div>
                        ) : null}
                      </div>
                    </div>

                    {/* column 2 — formation column */}
                    <div
                      className="relative z-10 shrink-0 border-r border-line bg-surface-2"
                      style={{ width: FORMATION_COL }}
                    >
                      <ColumnHeader
                        title={
                          <span className="flex items-center gap-1.5">
                            Formation
                            {centreFormationIsProxy ? (
                              <ProxyTag>15/9 proxy</ProxyTag>
                            ) : null}
                          </span>
                        }
                        subtitle="derived depth bands"
                        tone="muted"
                      />
                      <div className="relative" style={{ height: PLOT_H }}>
                        {data.formation_column.map((f) => {
                          const top = yPct(f.top_depth)
                          const bottom = yPct(f.bottom_depth)
                          const visibleTop = Math.min(top, bottom)
                          const visibleHeight = Math.max(Math.abs(bottom - top), 0.8)
                          if (visibleTop > 108 || visibleTop + visibleHeight < -8) return null
                          return (
                            <div
                              key={f.name}
                              className={cx(
                                'absolute inset-x-0 flex flex-col justify-start border-y border-line px-2 py-1',
                                // Tinted, not solid: the column is stratigraphy,
                                // so the current band must stay readable as a band.
                                f.current
                                  ? 'border-l-4 border-l-accent bg-accent-soft text-fg-strong'
                                  : 'bg-[var(--plot-bg)] text-fg-muted',
                              )}
                              style={{
                                top: `${visibleTop}%`,
                                height: `${visibleHeight}%`,
                              }}
                            >
                              <span className="truncate text-2xs font-semibold uppercase tracking-wider">
                                {f.name}
                              </span>
                              {visibleHeight > 9 ? (
                                <span className="tnum text-2xs opacity-80">
                                  {fmtNumber(f.top_depth, 0)} – {fmtNumber(f.bottom_depth, 0)} m
                                </span>
                              ) : null}
                              {f.current ? (
                                <span className="text-2xs font-semibold text-accent">
                                  current · proxy
                                </span>
                              ) : null}
                            </div>
                          )
                        })}
                      </div>
                    </div>

                    {/* columns 3..n — one per selected offset well */}
                    {shownWells.length === 0 ? (
                      <div
                        className="flex items-center px-6 text-xs text-fg-subtle"
                        style={{ height: PLOT_H }}
                      >
                        No offset wells selected. Tick one in the rail on the right.
                      </div>
                    ) : (
                      shownWells.map((o) => (
                        <OffsetColumn
                          key={o.well.id}
                          offset={o}
                          yPct={yPct}
                          onHover={setTip}
                          onOpen={(id) => openEvidence(id)}
                        />
                      ))
                    )}
                  </div>
                </div>
              </div>
            </div>
          ) : null}
        </ResourceState>

        {data ? (
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-line pt-2">
            <LegendSwatch
              className="border-2 border-warn bg-warn-soft"
              label="Current position"
            />
            <LegendSwatch color="var(--band-critical)" label="Critical" />
            <LegendSwatch color="var(--band-high)" label="High" />
            <LegendSwatch color="var(--band-warning)" label="Moderate" />
            <LegendSwatch color="var(--ok)" label="Low" />
            <span className="ml-auto text-2xs text-fg-subtle">
              Hover an event for its record · click to open the evidence chain
            </span>
          </div>
        ) : null}
      </SectionCard>

      {/* ================== right rail: everything that drives it ============== */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex min-w-0 flex-col gap-3">
          <SectionCard title="Offset columns" dense>
            <p className="mb-1.5 text-2xs text-fg-subtle">
              Ticked wells are plotted on the shared TVD scale. The selection is kept in{" "}
              <span className="tnum">?offsets=</span> so a replay can be shared as a link.
            </p>
            {offsetWells.length === 0 ? (
              <EmptyState
                title="No offset wells returned"
                hint="Nothing matched this radius and event-type filter."
              />
            ) : (
              <ul className="divide-y divide-line">
                {offsetWells.map((o) => {
                  const on = selectedIds.includes(o.well.id)
                  return (
                    <li key={o.well.id} className="flex items-center gap-2.5 py-1.5">
                      <input
                        id={`col-${o.well.id}`}
                        type="checkbox"
                        className="size-3.5 accent-accent"
                        checked={on}
                        onChange={() => toggleOffset(o.well.id)}
                      />
                      <label
                        htmlFor={`col-${o.well.id}`}
                        className="min-w-0 flex-1 cursor-pointer"
                      >
                        <span className="text-xs font-semibold text-fg-strong">
                          {o.well.name}
                        </span>
                        <span className="tnum ml-2 text-2xs text-fg-muted">
                          {`${o.distance_km.toFixed(1)} km · ${fmtPercent(o.relevance_score, 0)} · ${o.events.length} events`}
                        </span>
                      </label>
                      <div className="w-20 shrink-0">
                        <Meter
                          value={o.relevance_score}
                          fillClass="bg-accent"
                          height={5}
                          label={`${o.well.name} relevance ${o.relevance_score.toFixed(2)}`}
                        />
                      </div>
                      <Badge tone={ALIGNMENT_TONE[o.formation_alignment] ?? 'muted'}>
                        {o.formation_alignment}
                      </Badge>
                    </li>
                  )
                })}
              </ul>
            )}
          </SectionCard>

          <SectionCard
            title="Recurring hazards"
            description="Event types seen across the plotted offsets inside this TVD window."
            dense
          >
            {(data?.recurring_hazards.length ?? 0) === 0 ? (
              <p className="py-2 text-xs text-fg-subtle">
                No hazard type recurs across the plotted offset wells in this window.
              </p>
            ) : (
              <DataTable
                rows={data!.recurring_hazards}
                rowKey={(h) => h.event_type}
                columns={[
                  {
                    key: 'type',
                    header: 'Hazard',
                    render: (h) => (
                      <span className="flex items-center gap-1.5 font-medium text-fg-strong">
                        <span
                          className="h-2 w-2 shrink-0 rounded-sm"
                          style={{
                            backgroundColor: severityFillVar(
                              h.event_type === 'KICK' ? 'HIGH' : 'MODERATE',
                            ),
                          }}
                        />
                        {h.event_label}
                      </span>
                    ),
                  },
                  {
                    key: 'interval',
                    header: 'TVD interval',
                    align: 'right',
                    render: (h) => (
                      <span className="tnum">
                        {`${fmtNumber(h.tvd_interval.top, 0)} – ${fmtNumber(h.tvd_interval.bottom, 0)} m`}
                      </span>
                    ),
                  },
                  {
                    key: 'depth_below_current_m',
                    header: 'Vs current',
                    align: 'right',
                    render: (h) => (
                      <span className="tnum">
                        {h.depth_below_current_m === null ? '—' : `${h.depth_below_current_m} m`}
                      </span>
                    ),
                  },
                  {
                    key: 'support',
                    header: 'Wells / events',
                    align: 'right',
                    render: (h) => (
                      <span className="tnum">
                        {h.well_count} / {h.event_count}
                      </span>
                    ),
                  },
                ]}
              />
            )}
            {(data?.recurring_hazards.length ?? 0) > 0 ? (
              <div className="mt-2 flex flex-wrap gap-1">
                {(data?.recurring_hazards ?? []).map((h) => (
                  <span
                    key={h.event_type}
                    className="inset-surface px-1.5 py-0.5 text-2xs text-fg-muted"
                  >
                    {h.wells.join(', ')}
                  </span>
                ))}
              </div>
            ) : null}
          </SectionCard>

        </div>

        {/* ---------------------------- the rail ----------------------------- */}
        {/* Everything that drives the hero lives here, in the order a user
            reaches for it: window → what to plot in it → what is open on the
            well. Contract §4.3 puts the alert strip in the rail, not beside
            the plot, so nothing competes with the shared TVD scale. */}
        <div className="flex min-w-0 flex-col gap-3">
          <SectionCard title="Plot window" description="TVD range and density on the shared scale." dense>
            <div className="flex flex-col gap-2.5">
              <div className="flex flex-col gap-1.5">
                <span className={LABEL}>Window (TVD, m)</span>
                <div className="flex items-center gap-1.5">
                  <WindowStepper
                    label="Window top TVD"
                    value={Math.round(topTvd)}
                    onChange={(next) => setTopOverride(next)}
                    onStep={(delta) => step('top', delta)}
                  />
                  <span className="text-2xs text-fg-subtle">to</span>
                  <WindowStepper
                    label="Window bottom TVD"
                    value={Math.round(bottomTvd)}
                    onChange={(next) => setBottomOverride(next)}
                    onStep={(delta) => step('bottom', delta)}
                  />
                </div>
              </div>

              <div className="flex flex-wrap gap-1.5">
                <button type="button" className={ghostButtonClass} onClick={zoomToEvents}>
                  <LineIcon name="filter" size={13} />
                  Zoom to events
                </button>
                <button type="button" className={ghostButtonClass} onClick={snapToCurrent}>
                  <LineIcon name="compass" size={13} />
                  Snap to current
                </button>
                <button
                  type="button"
                  className={ghostButtonClass}
                  onClick={() => {
                    setTopOverride(null)
                    setBottomOverride(null)
                  }}
                >
                  <LineIcon name="refresh" size={13} />
                  Reset
                </button>
              </div>

              {/* Density is an explicit control, not a hidden truncation: the
                  real corpus puts ~316 events in a default window, and painting
                  them all makes the hero unreadable rather than informative. */}
              <div className="flex flex-col gap-1.5">
                <span className={LABEL}>Events painted per column</span>
                <SegmentedControl
                  label="Events painted per column"
                  size="sm"
                  value={chipCapId}
                  options={CHIP_CAP_CHOICES.map((c) => ({ id: c.id, label: c.label }))}
                  onChange={setChipCapId}
                />
              </div>

              <div className="flex items-end gap-3">
                <div className="w-32">
                  <label className={LABEL} htmlFor="replay-radius">
                    Search radius (km)
                  </label>
                  <select
                    id="replay-radius"
                    className={cx(controlClass, 'mt-1 w-full')}
                    value={radiusKm}
                    onChange={(e) => setRadiusKm(Number(e.target.value))}
                  >
                    {RADIUS_CHOICES.map((v) => (
                      <option key={v} value={v}>
                        {v} km
                      </option>
                    ))}
                  </select>
                </div>
                <p className="tnum flex-1 text-2xs leading-snug text-fg-subtle">
                  {`${fmtMeters(topTvd)} – ${fmtMeters(bottomTvd)} TVD · ${eventCount} events in window`}
                </p>
              </div>
            </div>
          </SectionCard>

          <SectionCard title="Event type" dense>
            <SegmentedControl
              label="Event type"
              size="sm"
              value={eventType}
              options={eventTypeOptions}
              onChange={setEventType}
            />
          </SectionCard>

          <SectionCard
            title="Alerts on this well"
            dense
            actions={
              <Link to="/alerts" className={ghostButtonClass}>
                All
                <LineIcon name="arrowRight" size={12} />
              </Link>
            }
          >
            {(data?.alerts.length ?? 0) === 0 ? (
              <p className="py-1 text-xs text-fg-subtle">
                No active alerts for this well in the current record.
              </p>
            ) : (
              <ul className="flex flex-col gap-2">
                {data!.alerts.map((a) => (
                  <li key={a.id} className="inset-surface px-2.5 py-2">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span
                        className={cx('size-2 shrink-0 rounded-sm', bandDotClass(a.severity_band))}
                      />
                      <span className="text-xs font-semibold text-fg-strong">{a.title}</span>
                      <Badge tone={bandTone(a.severity_band)}>{a.severity_band}</Badge>
                      <span className="tnum text-2xs text-fg-muted">
                        {`risk ${a.risk_score.toFixed(2)} · ${fmtMeters(a.interval.top_tvd)}–${fmtMeters(a.interval.bottom_tvd)}`}
                      </span>
                    </div>
                    {/* Telemetry alerts have no supporting offset wells, and that
                        is a property of the source, not a gap. */}
                    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                      <AlertEvidence alert={a} />
                      {isTelemetryAlert(a) ? <ProxyTag>Telemetry</ProxyTag> : null}
                      <button
                        type="button"
                        className="ml-auto text-2xs font-semibold text-accent underline underline-offset-2"
                        onClick={() => openDecision(a.id)}
                      >
                        Decision panel
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>

          {/* Reference data, closed. Not wrapped in a card: a card whose only
              content is a disclosure is a fourth level of chrome. */}
          <Disclosure summary="Drilling parameters and method">
            <dl className="space-y-1.5">
              <RailField
                label="Current MD"
                value={fmtMeters(currentWell?.current_depth_md)}
              />
              <RailField
                label="Mud weight"
                value={`${fmtNumber(currentWell?.operating_context?.mud_weight_ppg, 1)} ppg`}
              />
              <RailField
                label="ROP"
                value={`${fmtNumber(currentWell?.operating_context?.rop_mph, 1)} m/h`}
              />
              <RailField
                label="WOB"
                value={`${fmtNumber(currentWell?.operating_context?.wob_klb, 1)} klb`}
              />
              <RailField
                label="Hole size"
                value={currentWell?.operating_context?.bit_size ?? '—'}
              />
              <RailField
                label="Method"
                value={scoreMethodLabel(data?.method)}
              />
            </dl>
            <p className="mt-2 text-2xs leading-relaxed text-fg-subtle">
              Relevance, similarity and risk values are returned by the engine; nothing on this
              screen is recomputed in the browser.
            </p>
          </Disclosure>
        </div>

      </div>

      {tip ? (
        <div
          className="panel-surface pointer-events-none fixed z-50 w-80 p-3"
          style={{
            left: Math.min(tip.x + 16, window.innerWidth - 340),
            top: tip.y + 16,
          }}
          role="tooltip"
        >
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-semibold text-fg-strong">{tip.event.event_label}</p>
            <Badge tone={severityTone(tip.event.severity)}>{tip.event.severity}</Badge>
          </div>
          <p className="tnum mt-0.5 text-2xs text-fg-muted">
            {`${tip.event.id} · ${tip.event.well_name} · MD ${fmtMeters(tip.event.md)} · TVD ${fmtMeters(tip.event.tvd)}`}
          </p>
          <p className="mt-1 text-2xs text-fg-muted">
            {`${tip.event.formation || '—'} · ${humanizeEnum(tip.event.event_type)} · ${fmtDate(tip.event.occurred_at)}`}
          </p>
          {tip.event.description ? (
            <p className="mt-1.5 text-2xs leading-relaxed text-fg">{tip.event.description}</p>
          ) : null}
          {tip.event.mitigation ? (
            <p className="inset-surface mt-1.5 px-2 py-1.5 text-2xs leading-relaxed text-fg">
              <span className="font-semibold text-fg-strong">Mitigation: </span>
              {tip.event.mitigation}
            </p>
          ) : null}
          <p className="mt-1.5 text-2xs text-fg-subtle">
            {tip.event.document
              ? `${tip.event.document.doc_type_label} · ${tip.event.document.filename}`
              : 'No source document attached'}
          </p>
        </div>
      ) : null}
    </div>
  )
}

/* ------------------------------------------------------------------ *
 * Small rail pieces
 * ------------------------------------------------------------------ */

/** One of the three values in the plot header. */
function PlotHeaderStat({
  label,
  value,
  tone,
}: {
  label: string
  value: ReactNode
  tone?: 'warning'
}) {
  return (
    <span className="flex min-w-0 flex-col items-end leading-tight">
      <span className="text-2xs font-semibold uppercase tracking-[0.06em] text-fg-muted">
        {label}
      </span>
      <span
        className={cx(
          'flex max-w-[210px] items-center justify-end gap-1.5 text-sm font-semibold',
          tone === 'warning' ? 'text-warn' : 'text-fg-strong',
        )}
      >
        {value}
      </span>
    </span>
  )
}

function WindowStepper({
  label,
  value,
  onChange,
  onStep,
}: {
  label: string
  value: number
  onChange: (next: number) => void
  onStep: (delta: number) => void
}) {
  return (
    <div className="flex items-center">
      <input
        aria-label={label}
        inputMode="numeric"
        className={cx(controlClass, 'tnum w-[72px] rounded-r-none text-right')}
        value={value}
        onChange={(e) => onChange(Number(e.target.value.replace(/[^\d]/g, '') || 0))}
      />
      <button
        type="button"
        className={cx(ghostButtonClass, 'h-7 w-7 rounded-none border-l-0 px-0')}
        onClick={() => onStep(-25)}
        aria-label={`Decrease ${label.toLowerCase()}`}
      >
        −
      </button>
      <button
        type="button"
        className={cx(ghostButtonClass, 'h-7 w-7 rounded-l-none border-l-0 px-0')}
        onClick={() => onStep(25)}
        aria-label={`Increase ${label.toLowerCase()}`}
      >
        +
      </button>
    </div>
  )
}

function RailField({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line pb-1 last:border-b-0">
      <dt className="text-2xs font-medium uppercase tracking-wider text-fg-muted">{label}</dt>
      <dd className="tnum text-xs text-fg-strong">{value}</dd>
    </div>
  )
}

function ColumnHeader({
  title,
  subtitle,
  tone,
}: {
  title: ReactNode
  subtitle: string
  tone: 'current' | 'muted'
}) {
  return (
    <div
      className={cx(
        'flex flex-col justify-center border-b border-line px-2',
        tone === 'current' ? 'bg-chrome text-chrome-fg' : 'bg-surface-3 text-fg-muted',
      )}
      style={{ height: HEADER_H }}
    >
      <span className="truncate text-2xs font-semibold uppercase tracking-wider">{title}</span>
      <span className="truncate text-2xs opacity-80">{subtitle}</span>
    </div>
  )
}

function OffsetColumn({
  offset,
  yPct,
  onHover,
  onOpen,
}: {
  offset: ReplayOffsetWell
  yPct: (depth: number) => number
  onHover: (tip: Tip | null) => void
  onOpen: (eventId: string) => void
}) {
  return (
    <div
      className="relative z-10 shrink-0 border-r border-line bg-[var(--plot-bg)]"
      style={{ width: OFFSET_COL }}
    >
      <div
        className="flex flex-col justify-center border-b border-line bg-surface-3 px-2"
        style={{ height: HEADER_H }}
      >
        <span className="truncate text-2xs font-semibold uppercase tracking-wider text-fg-strong">
          {offset.well.name}
        </span>
        <span className="tnum truncate text-2xs text-fg-muted">
          {`${offset.distance_km.toFixed(1)} km · ${fmtPercent(offset.relevance_score, 0)}`}
        </span>
      </div>
      <div className="flex items-center gap-1 border-b border-line px-2 py-1">
        <Badge tone={ALIGNMENT_TONE[offset.formation_alignment] ?? 'muted'}>
          {offset.formation_alignment}
        </Badge>
        <span className="truncate text-2xs text-fg-muted">
          {offset.well.current_formation?.name}
        </span>
      </div>
      <div className="relative" style={{ height: PLOT_H - 22 }}>
        {offset.events.map((ev) => (
          <EventChip
            key={ev.id}
            event={ev}
            topPct={yPct(ev.tvd)}
            onHover={onHover}
            onOpen={() => onOpen(ev.id)}
          />
        ))}
        {offset.events.length === 0 ? (
          <p className="px-2 pt-2 text-2xs text-fg-subtle">No events in this window.</p>
        ) : null}
      </div>
    </div>
  )
}

function EventChip({
  event,
  topPct,
  onHover,
  onOpen,
}: {
  event: EventDetail
  topPct: number
  onHover: (tip: Tip | null) => void
  onOpen: () => void
}) {
  return (
    <button
      type="button"
      className="absolute inset-x-1 z-10 flex -translate-y-1/2 items-center gap-1.5 rounded-sm border border-line bg-[var(--plot-bg)] px-1.5 py-1 text-left transition-shadow hover:shadow-[var(--shadow-raised)]"
      style={{ top: `${topPct}%` }}
      onMouseEnter={(e) => onHover({ event, x: e.clientX, y: e.clientY })}
      onMouseMove={(e) => onHover({ event, x: e.clientX, y: e.clientY })}
      onMouseLeave={() => onHover(null)}
      onFocus={(e) =>
        onHover({
          event,
          x: e.currentTarget.getBoundingClientRect().left,
          y: e.currentTarget.getBoundingClientRect().bottom,
        })
      }
      onBlur={() => onHover(null)}
      onClick={onOpen}
    >
      <span
        className="h-6 w-1 shrink-0 rounded-sm"
        style={{ backgroundColor: severityFillVar(event.severity) }}
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-2xs font-semibold text-fg-strong">
          {event.event_label}
        </span>
        <span className="tnum block truncate text-2xs text-fg-muted">
          {`${fmtNumber(event.tvd, 0)} m · ${event.formation || '—'}`}
        </span>
      </span>
    </button>
  )
}

export default OffsetReplay
