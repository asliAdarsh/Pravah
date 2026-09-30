import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Api } from '@/api/client'
import type { OffsetWell } from '@/api/types'
import {
  Badge,
  DataTable,
  Disclosure,
  EmptyState,
  KeyValueGrid,
  LineIcon,
  Meter,
  SectionCard,
  Toolbar,
  WhyFactors,
  actionButtonClass,
  controlClass,
  cx,
  ghostButtonClass,
} from '@/components/ui'
import { useAsyncData } from '@/lib/useAsyncData'
import { useApp } from '@/store/useApp'
import {
  fmtDate,
  fmtKm,
  fmtMeters,
  fmtNumber,
  fmtPercent,
  humanizeEnum,
  relevanceTone,
  severityTone,
  wellStatusTone,
} from '@/lib/format'
import {
  Field,
  LABEL,
  LegendSwatch,
  ResourceState,
  ScreenHeader,
  clamp,
  niceStep,
  relevanceFillVar,
  severityFillVar,
} from './_shared'
import { ProxyTag, SliceNote, isProxyPosition, useBounded } from './_calm'

/* ------------------------------------------------------------------ *
 * Screen 2 — Offset Intelligence / Well Location Plan
 *
 * Anatomy (UX contract §4.2): the plan is the HERO and takes the full
 * remaining width; the right rail shows EITHER the selected well's record OR
 * the ranking list, never both. Every filter lives in one bar above the map,
 * the three loose view buttons are in an overflow menu, and the relevance
 * weights live in Settings with a single link back to them.
 *
 * The plan is hand-built SVG on purpose. No tile server and no map library:
 * the prototype has to stay usable in a demo / hackathon environment with no
 * network egress, so coordinates are projected locally (equirectangular, which
 * is accurate to well under a percent over the < 25 km radius used here) and
 * the graticule, scale bar, north arrow and radius ring are plain SVG.
 * ------------------------------------------------------------------ */

const VIEW_W = 1000
const VIEW_H = 620
const CX = VIEW_W / 2
const CY = VIEW_H / 2
const KM_PER_DEG_LAT = 110.574

const STATUS_OPTIONS = ['PLANNED', 'DRILLING', 'SUSPENDED', 'COMPLETED', 'ABANDONED'] as const

const MIN_RELEVANCE_CHOICES = [0, 0.05, 0.15, 0.25, 0.4, 0.6]

interface ViewState {
  scale: number
  ox: number
  oy: number
}

export function OffsetMap() {
  const {
    meta,
    currentWell,
    currentWellId,
    weights,
    relevanceConfig,
    selectedOffsetWellIds,
    toggleOffsetWell,
    openEvidence,
    notify,
  } = useApp()
  const navigate = useNavigate()
  const svgRef = useRef<SVGSVGElement | null>(null)
  const dragRef = useRef<{
    clientX: number
    clientY: number
    ox: number
    oy: number
    width: number
    height: number
  } | null>(null)

  const [radiusOverride, setRadiusOverride] = useState<number | null>(null)
  const [minRelOverride, setMinRelOverride] = useState<number | null>(null)
  const [formation, setFormation] = useState('')
  const [eventType, setEventType] = useState('')
  const [status, setStatus] = useState('')
  const [depthMin, setDepthMin] = useState('')
  const [depthMax, setDepthMax] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const [view, setView] = useState<ViewState>({ scale: 40, ox: 0, oy: 0 })

  const radiusKm = radiusOverride ?? relevanceConfig?.radius_km ?? 8
  const minRelevance = minRelOverride ?? relevanceConfig?.min_relevance ?? 0.15
  const fitScale = clamp(300 / Math.max(radiusKm, 0.5), 4, 4000)

  const numericDepthMin = depthMin.trim() === '' ? undefined : Number(depthMin)
  const numericDepthMax = depthMax.trim() === '' ? undefined : Number(depthMax)

  const nearby = useAsyncData(
    (signal) =>
      Api.nearby(
        currentWellId,
        {
          radius_km: radiusKm,
          min_relevance: minRelevance,
          event_type: eventType || undefined,
          formation: formation || undefined,
          status: status || undefined,
          depth_min_md: Number.isFinite(numericDepthMin) ? numericDepthMin : undefined,
          depth_max_md: Number.isFinite(numericDepthMax) ? numericDepthMax : undefined,
        },
        signal,
      ),
    [currentWellId, radiusKm, minRelevance, eventType, formation, status, depthMin, depthMax],
  )

  const formations = useAsyncData((signal) => Api.formations(signal), [])

  const items = nearby.data?.items ?? []
  /* At a 25 km radius the real corpus returns dozens of wells, so the ranking
     rail is bounded like every other list. The plan still plots all of them. */
  const ranking = useBounded(items, 12, 20)
  /* Only the full well record carries the provenance note; the summary shape
     inside `/nearby` does not, so fall back to no marker rather than guessing. */
  const centrePositionIsProxy = isProxyPosition(currentWell)
  const centre = currentWell ?? nearby.data?.current_well ?? null
  const lat0 = Number.isFinite(centre?.latitude) ? centre!.latitude : 0
  const lon0 = Number.isFinite(centre?.longitude) ? centre!.longitude : 0
  const kmPerDegLon = 111.32 * Math.cos((lat0 * Math.PI) / 180)
  const selected = useMemo(
    () => items.find((o) => o.well.id === selectedId) ?? null,
    [items, selectedId],
  )

  /* Re-frame whenever the search radius changes. */
  useEffect(() => {
    setView({ scale: fitScale, ox: 0, oy: 0 })
  }, [fitScale])

  /* Wheel zoom is bound natively: React's delegated onWheel listener is
     passive, so preventDefault() inside it would not stop page scroll. */
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const onWheel = (ev: WheelEvent) => {
      const box = svg.getBoundingClientRect()
      if (box.width === 0 || box.height === 0) return
      ev.preventDefault()
      const px = ((ev.clientX - box.left) / box.width) * VIEW_W
      const py = ((ev.clientY - box.top) / box.height) * VIEW_H
      const factor = Math.exp(-ev.deltaY * 0.0015)
      setView((v) => {
        const next = clamp(v.scale * factor, 4, 6000)
        const gx = (px - CX - v.ox) / v.scale
        const gy = (py - CY - v.oy) / v.scale
        return {
          scale: next,
          ox: px - CX - gx * next,
          oy: py - CY - gy * next,
        }
      })
    }
    svg.addEventListener('wheel', onWheel, { passive: false })
    return () => svg.removeEventListener('wheel', onWheel)
  }, [])

  const project = (lat: number, lon: number) => ({
    x: (lon - lon0) * kmPerDegLon,
    y: -(lat - lat0) * KM_PER_DEG_LAT,
  })

  const origin = { x: CX + view.ox, y: CY + view.oy }
  const { scale } = view

  /* Graticule, in kilometres relative to the current well. */
  const gridKm = niceStep(72 / scale)
  const minKx = Math.floor((-CX - view.ox) / (gridKm * scale))
  const maxKx = Math.ceil((CX - view.ox) / (gridKm * scale))
  const minKy = Math.floor((-CY - view.oy) / (gridKm * scale))
  const maxKy = Math.ceil((CY - view.oy) / (gridKm * scale))
  const xLines: number[] = []
  for (let k = minKx; k <= maxKx && xLines.length < 40; k += 1) xLines.push(k)
  const yLines: number[] = []
  for (let k = minKy; k <= maxKy && yLines.length < 40; k += 1) yLines.push(k)

  const barKm = niceStep(120 / scale)
  const visible = items.filter((o) => {
    if (!Number.isFinite(o.well.latitude) || !Number.isFinite(o.well.longitude)) return false
    const p = project(o.well.latitude, o.well.longitude)
    const sx = origin.x + p.x * scale
    const sy = origin.y + p.y * scale
    return sx > -60 && sx < VIEW_W + 60 && sy > -60 && sy < VIEW_H + 60
  })

  const onDragStart = (e: ReactPointerEvent<SVGRectElement>) => {
    const box = svgRef.current?.getBoundingClientRect()
    if (!box) return
    e.currentTarget.setPointerCapture(e.pointerId)
    setDragging(true)
    dragRef.current = {
      clientX: e.clientX,
      clientY: e.clientY,
      ox: view.ox,
      oy: view.oy,
      width: box.width,
      height: box.height,
    }
  }
  const onDragMove = (e: ReactPointerEvent<SVGRectElement>) => {
    const d = dragRef.current
    if (!d) return
    const dx = ((e.clientX - d.clientX) / d.width) * VIEW_W
    const dy = ((e.clientY - d.clientY) / d.height) * VIEW_H
    setView((v) => ({ ...v, ox: d.ox + dx, oy: d.oy + dy }))
  }
  const onDragEnd = (e: ReactPointerEvent<SVGRectElement>) => {
    dragRef.current = null
    setDragging(false)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
  }

  const resetFilters = () => {
    setFormation('')
    setEventType('')
    setStatus('')
    setDepthMin('')
    setDepthMax('')
    setMinRelOverride(null)
    setRadiusOverride(null)
  }

  const applyRadiusToEngine = async () => {
    try {
      await Api.setRelevanceConfig({
        weights,
        radius_km: radiusKm,
        min_relevance: minRelevance,
      })
      notify(
        `Relevance engine updated — radius ${radiusKm} km, min relevance ${minRelevance}`,
        'success',
      )
    } catch (err) {
      notify(
        err instanceof Error ? err.message : 'Could not persist the relevance configuration',
        'critical',
      )
    }
  }

  const openReplay = (offset: OffsetWell) => {
    const ids = [offset.well.id, ...selectedOffsetWellIds.filter((id) => id !== offset.well.id)]
    navigate(`/offset-intelligence/replay?well=${currentWellId}&offsets=${ids.join(',')}`)
  }

  const xLabel = (k: number) => {
    const km = k * gridKm
    if (km === 0) return '0'
    return `${km > 0 ? '+' : ''}${Number(km.toFixed(2))} km E`
  }
  const yLabel = (k: number) => {
    const km = k * gridKm
    return km === 0 ? '' : `${Number(km.toFixed(2))} km N`
  }

  return (
    <div className="flex flex-col gap-3 p-3">
      <ScreenHeader
        title="Offset location plan"
        subtitle="Where the offset wells are, relative to the current well. Scores, bands and factors come from the relevance engine."
        actions={
          <>
            <Link to="/offset-intelligence/replay" className={ghostButtonClass}>
              <LineIcon name="replay" size={13} />
              Depth replay
            </Link>
            <OverflowMenu label="Plan actions">
              <MenuItem
                onClick={() => setView({ scale: fitScale, ox: 0, oy: 0 })}
                hint="Re-frame the plan on the current search radius"
              >
                Fit radius
              </MenuItem>
              <MenuItem onClick={resetFilters} hint="Clear every filter except the radius">
                Reset filters
              </MenuItem>
              <MenuItem
                onClick={applyRadiusToEngine}
                hint="Persist this radius and minimum relevance as the engine default"
              >
                Apply radius to engine
              </MenuItem>
            </OverflowMenu>
          </>
        }
      />

      {/* ------------------------- one filter bar ----------------------------- */}
      <Toolbar className="flex flex-wrap items-end gap-x-4 gap-y-2">
        <div className="w-52">
          <label className={LABEL} htmlFor="map-radius">
            Search radius (km)
          </label>
          <div className="mt-1 flex items-center gap-2">
            <input
              id="map-radius"
              type="range"
              min={1}
              max={25}
              step={0.5}
              value={radiusKm}
              className="h-7 w-full accent-accent"
              onChange={(e) => setRadiusOverride(Number(e.target.value))}
            />
            <span className="tnum w-11 shrink-0 text-right text-xs font-semibold text-fg-strong">
              {radiusKm.toFixed(1)}
            </span>
          </div>
        </div>

        <div className="w-36">
          <label className={LABEL} htmlFor="map-minrel">
            Min relevance
          </label>
          <select
            id="map-minrel"
            className={cx(controlClass, 'mt-1 w-full')}
            value={minRelevance}
            onChange={(e) => setMinRelOverride(Number(e.target.value))}
          >
            {MIN_RELEVANCE_CHOICES.map((v) => (
              <option key={v} value={v}>
                {v === 0 ? 'Show all' : `≥ ${v.toFixed(2)}`}
              </option>
            ))}
          </select>
        </div>

        <div className="w-44">
          <label className={LABEL} htmlFor="map-formation">
            Formation
          </label>
          <select
            id="map-formation"
            className={cx(controlClass, 'mt-1 w-full')}
            value={formation}
            onChange={(e) => setFormation(e.target.value)}
          >
            <option value="">All formations</option>
            {(formations.data?.items ?? []).map((f) => (
              <option key={f.id} value={f.name}>
                {f.name}
              </option>
            ))}
          </select>
        </div>

        <div className="w-44">
          <label className={LABEL} htmlFor="map-event-type">
            Event type
          </label>
          <select
            id="map-event-type"
            className={cx(controlClass, 'mt-1 w-full')}
            value={eventType}
            onChange={(e) => setEventType(e.target.value)}
          >
            <option value="">All event types</option>
            {(meta?.event_types ?? []).map((t) => (
              <option key={t.code} value={t.code}>
                {t.label}
              </option>
            ))}
          </select>
        </div>

        <div className="w-36">
          <label className={LABEL} htmlFor="map-status">
            Well status
          </label>
          <select
            id="map-status"
            className={cx(controlClass, 'mt-1 w-full')}
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="">Any status</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {humanizeEnum(s)}
              </option>
            ))}
          </select>
        </div>

        <div className="w-44">
          <label className={LABEL} htmlFor="map-depth-min">
            Depth range (MD, m)
          </label>
          <div className="mt-1 flex items-center gap-1">
            <input
              id="map-depth-min"
              inputMode="numeric"
              placeholder="min"
              aria-label="Minimum measured depth"
              className={cx(controlClass, 'w-full')}
              value={depthMin}
              onChange={(e) => setDepthMin(e.target.value.replace(/[^\d.]/g, ''))}
            />
            <span className="text-2xs text-fg-subtle">–</span>
            <input
              inputMode="numeric"
              placeholder="max"
              aria-label="Maximum measured depth"
              className={cx(controlClass, 'w-full')}
              value={depthMax}
              onChange={(e) => setDepthMax(e.target.value.replace(/[^\d.]/g, ''))}
            />
          </div>
        </div>

        <p className="ml-auto max-w-xs text-2xs leading-snug text-fg-subtle">
          Filters re-query <code>/wells/&#123;id&#125;/nearby</code> only. Weights in use:{' '}
          <Link to="/settings" className="text-accent underline underline-offset-2">
            relevance engine settings
          </Link>
        </p>
      </Toolbar>

      {/* ================== HERO — the plan, plus one rail ==================== */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_380px]">
        <SectionCard
          title={
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span>Well location plan</span>
              <span aria-hidden className="text-fg-subtle">
                ·
              </span>
              <span className="font-medium text-fg-muted">{centre?.name ?? currentWellId}</span>
              {centre && Number.isFinite(centre.latitude) ? (
                <span className="tnum text-2xs font-normal text-fg-subtle">
                  {`${centre.latitude.toFixed(3)}°N ${centre.longitude.toFixed(3)}°E`}
                </span>
              ) : null}
              {centrePositionIsProxy ? <ProxyTag>Derived position</ProxyTag> : null}
            </span>
          }
          description={`${visible.length} of ${items.length} real NPD wells plotted inside ${radiusKm.toFixed(1)} km · ${centre ? fmtMeters(centre.current_depth_md) : '—'} MD · ${centre ? fmtMeters(centre.current_tvd) : '—'} TVD`}
          dense
        >
          <ResourceState
            loading={nearby.loading && !nearby.data}
            error={nearby.error}
            onRetry={nearby.reload}
            skeleton={<div className="skeleton-block h-[420px] w-full rounded-md" />}
          >
            <div className="w-full" style={{ aspectRatio: `${VIEW_W} / ${VIEW_H}` }}>
              <svg
                ref={svgRef}
                viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
                className={cx(
                  'plot-surface block h-full w-full select-none',
                  dragging ? 'cursor-grabbing' : 'cursor-grab',
                )}
                style={{ touchAction: 'none' }}
                role="img"
                aria-label={`Well location plan: ${centre?.name ?? 'current well'} and ${visible.length} offset wells`}
              >
                <title>Offset well location plan</title>

                {/* Drag-to-pan surface. It is painted FIRST so well markers
                    sit above it and keep receiving clicks. */}
                <rect
                  x={0}
                  y={0}
                  width={VIEW_W}
                  height={VIEW_H}
                  fill="transparent"
                  onPointerDown={onDragStart}
                  onPointerMove={onDragMove}
                  onPointerUp={onDragEnd}
                  onPointerCancel={onDragEnd}
                />

                {xLines.map((k) => (
                  <g key={`gx${k}`}>
                    <line
                      x1={origin.x + k * gridKm * scale}
                      y1={0}
                      x2={origin.x + k * gridKm * scale}
                      y2={VIEW_H}
                      className="stroke-[var(--grid)]"
                      strokeWidth={1}
                    />
                    {/* Skip the label that would land in the x-axis caption
                        row at the corner. */}
                    {origin.x + k * gridKm * scale + 3 < 44 ? null : (
                      <text
                        x={origin.x + k * gridKm * scale + 3}
                        y={VIEW_H - 6}
                        fontSize={10}
                        className="fill-[var(--fg-subtle)]"
                      >
                        {xLabel(k)}
                      </text>
                    )}
                  </g>
                ))}
                {yLines.map((k) => (
                  <g key={`gy${k}`}>
                    <line
                      x1={0}
                      y1={origin.y - k * gridKm * scale}
                      x2={VIEW_W}
                      y2={origin.y - k * gridKm * scale}
                      className="stroke-[var(--grid)]"
                      strokeWidth={1}
                    />
                    {/* Likewise for the y label nearest the bottom caption row. */}
                    {origin.y - k * gridKm * scale - 4 > VIEW_H - 20 ? null : (
                      <text
                        x={6}
                        y={origin.y - k * gridKm * scale - 4}
                        fontSize={10}
                        className="fill-[var(--fg-subtle)]"
                      >
                        {yLabel(k)}
                      </text>
                    )}
                  </g>
                ))}

                <circle
                  cx={origin.x}
                  cy={origin.y}
                  r={Math.max(radiusKm * scale, 1)}
                  fill="none"
                  className="stroke-[var(--accent)]"
                  strokeWidth={1.5}
                  strokeDasharray="7 5"
                  opacity={0.75}
                />
                {/* The label is clamped inside the viewBox: on a wide radius
                    ring it would otherwise sit above y=0 and be clipped. */}
                <text
                  x={origin.x}
                  y={Math.max(14, origin.y - Math.max(radiusKm * scale, 1) - 6)}
                  fontSize={11}
                  textAnchor="middle"
                  className="fill-[var(--accent)]"
                  fontWeight={600}
                >
                  {`RADIUS ${radiusKm.toFixed(1)} km`}
                </text>

                {visible.map((o) => {
                  const p = project(o.well.latitude, o.well.longitude)
                  const sx = origin.x + p.x * scale
                  const sy = origin.y + p.y * scale
                  const r = 5 + clamp(o.relevance_score, 0, 1) * 7
                  const isSelected = o.well.id === selectedId
                  return (
                    <g
                      key={o.well.id}
                      onClick={() => setSelectedId(o.well.id)}
                      style={{ cursor: 'pointer' }}
                    >
                      {isSelected ? (
                        <circle
                          cx={sx}
                          cy={sy}
                          r={r + 7}
                          fill="none"
                          className="stroke-[var(--fg-strong)]"
                          strokeWidth={1.5}
                          opacity={0.7}
                        />
                      ) : null}
                      <circle
                        cx={sx}
                        cy={sy}
                        r={r}
                        fill={relevanceFillVar(o.relevance_score)}
                        fillOpacity={0.85}
                        stroke={isSelected ? 'var(--fg-strong)' : 'var(--plot-bg)'}
                        strokeWidth={isSelected ? 2.5 : 1.5}
                      />
                      <text
                        x={sx + r + 5}
                        y={sy + 2}
                        fontSize={10.5}
                        className="fill-[var(--fg-strong)]"
                        fontWeight={600}
                      >
                        {o.well.name}
                      </text>
                      {/* Distance is not repeated on the plan: it is in the
                          marker tooltip, the ranking list and the detail panel,
                          and the second label line collided with neighbouring
                          well names. */}
                      <title>
                        {`${o.well.name} — ${o.distance_km.toFixed(2)} km, relevance ${o.relevance_score.toFixed(2)} (${o.relevance_band})`}
                      </title>
                    </g>
                  )
                })}

                {/* The current well is the one mark that must out-read every
                    offset marker, so it is painted in --fg-strong: --chrome-2
                    is near-invisible against --plot-bg in dark. */}
                <g>
                  <circle
                    cx={origin.x}
                    cy={origin.y}
                    r={13}
                    className="fill-[var(--plot-bg)] stroke-[var(--fg-strong)]"
                    strokeWidth={2.5}
                  />
                  <line
                    x1={origin.x - 8}
                    y1={origin.y}
                    x2={origin.x + 8}
                    y2={origin.y}
                    className="stroke-[var(--fg-strong)]"
                    strokeWidth={1.5}
                  />
                  <line
                    x1={origin.x}
                    y1={origin.y - 8}
                    x2={origin.x}
                    y2={origin.y + 8}
                    className="stroke-[var(--fg-strong)]"
                    strokeWidth={1.5}
                  />
                  <circle cx={origin.x} cy={origin.y} r={3.5} className="fill-[var(--fg-strong)]" />
                  <g
                    transform={`translate(${clamp(origin.x + 18, 18, VIEW_W - 190)}, ${clamp(origin.y - 26, 8, VIEW_H - 42)})`}
                  >
                    <rect width={172} height={32} rx={3} className="fill-[var(--chrome-2)]" />
                    <text x={8} y={13} fontSize={10} className="fill-[var(--chrome-muted)]" fontWeight={600}>
                      CURRENT WELL
                    </text>
                    <text x={8} y={25} fontSize={11} className="fill-[var(--chrome-fg)]" fontWeight={600}>
                      {centre?.name ?? currentWellId}
                    </text>
                  </g>
                </g>

                <g transform={`translate(18, ${VIEW_H - 44})`}>
                  <line
                    x1={0}
                    y1={0}
                    x2={Math.max(barKm * scale, 6)}
                    y2={0}
                    className="stroke-[var(--fg-strong)]"
                    strokeWidth={2}
                  />
                  <line
                    x1={0}
                    y1={-4}
                    x2={0}
                    y2={4}
                    className="stroke-[var(--fg-strong)]"
                    strokeWidth={2}
                  />
                  <line
                    x1={Math.max(barKm * scale, 6)}
                    y1={-4}
                    x2={Math.max(barKm * scale, 6)}
                    y2={4}
                    className="stroke-[var(--fg-strong)]"
                    strokeWidth={2}
                  />
                  <text x={2} y={-8} fontSize={11} className="fill-[var(--fg-strong)]" fontWeight={600}>
                    {`${barKm} km`}
                  </text>
                </g>

                <g transform={`translate(${VIEW_W - 40}, 20)`}>
                  <line
                    x1={0}
                    y1={26}
                    x2={0}
                    y2={-6}
                    className="stroke-[var(--fg-strong)]"
                    strokeWidth={1.5}
                  />
                  <path d="M0 -14 L6 2 L0 -2 L-6 2 Z" className="fill-[var(--fg-strong)]" />
                  <text
                    x={0}
                    y={40}
                    fontSize={11}
                    textAnchor="middle"
                    className="fill-[var(--fg-strong)]"
                    fontWeight={600}
                  >
                    N
                  </text>
                </g>
              </svg>
            </div>

            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-line pt-2">
              <LegendSwatch color="var(--band-high)" label="High relevance" />
              <LegendSwatch color="var(--band-warning)" label="Medium relevance" />
              <LegendSwatch color="var(--band-info)" label="Low relevance" />
              <LegendSwatch
                className="border-2 border-[var(--fg-strong)] bg-[var(--plot-bg)]"
                label="Current well"
              />
              <LegendSwatch
                className="border border-dashed border-[var(--accent)]"
                label={`Radius ${radiusKm.toFixed(1)} km`}
              />
              <span className="ml-auto text-2xs text-fg-subtle">
                Drag to pan · wheel to zoom · click a well to inspect it
              </span>
            </div>
          </ResourceState>
        </SectionCard>

        {/* ------------------ rail: detail OR ranking, never both ------------- */}
        <div className="scroll-thin flex max-h-[calc(100vh-260px)] min-w-0 flex-col gap-3 overflow-y-auto xl:max-h-none">
          {selected ? (
            <OffsetDetail
              offset={selected}
              isSelected={selectedOffsetWellIds.includes(selected.well.id)}
              onToggle={() => toggleOffsetWell(selected.well.id)}
              onReplay={() => openReplay(selected)}
              onEvidence={() => {
                const ev = selected.relevant_events[0]
                if (ev) openEvidence(ev.id)
                else
                  notify(
                    'No evidence-backed event is attached to this offset in the record',
                    'warning',
                  )
              }}
              onClose={() => setSelectedId(null)}
            />
          ) : (
            <SectionCard
              title="Offset well ranking"
              description="Click a marker on the plan to replace this list with the well's full record."
              dense
            >
              <ResourceState
                loading={nearby.loading && !nearby.data}
                error={nearby.error}
                onRetry={nearby.reload}
              >
                {items.length === 0 ? (
                  <EmptyState
                    title="No offset wells match"
                    hint="Widen the search radius or clear a filter in the bar above."
                  />
                ) : (
                  <>
                    <DataTable<OffsetWell>
                      rows={ranking.visible}
                      rowKey={(o) => o.well.id}
                      onRowClick={(o) => setSelectedId(o.well.id)}
                      columns={[
                        {
                          key: 'well',
                          header: 'Offset well',
                          render: (o) => (
                            <span className="font-medium text-fg-strong">{o.well.name}</span>
                          ),
                        },
                        {
                          key: 'distance',
                          header: 'Dist.',
                          align: 'right',
                          render: (o) => <span className="tnum">{fmtKm(o.distance_km, 2)}</span>,
                        },
                        {
                          key: 'relevance',
                          header: 'Relevance',
                          align: 'right',
                          render: (o) => (
                            <span
                              className="tnum font-semibold"
                              style={{ color: relevanceFillVar(o.relevance_score) }}
                            >
                              {fmtPercent(o.relevance_score, 0)}
                            </span>
                          ),
                        },
                        {
                          key: 'band',
                          header: 'Band',
                          align: 'right',
                          render: (o) => (
                            <Badge tone={relevanceTone(o.relevance_score)}>{o.relevance_band}</Badge>
                          ),
                        },
                      ]}
                    />
                    <SliceNote
                      className="mt-2"
                      shown={ranking.shown}
                      total={ranking.total}
                      noun="offset wells"
                      onMore={ranking.more}
                    />
                  </>
                )}
              </ResourceState>
            </SectionCard>
          )}
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ *
 * Overflow menu — the three loose plan buttons, collapsed
 * ------------------------------------------------------------------ */

function OverflowMenu({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const container = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div ref={container} className="relative">
      <button
        type="button"
        className={ghostButtonClass}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <LineIcon name="filter" size={13} />
        {label}
        <LineIcon
          name="chevron"
          size={12}
          className={cx('transition-transform', open && 'rotate-90')}
        />
      </button>
      {open ? (
        <div
          role="menu"
          className="panel-surface absolute right-0 top-[calc(100%+4px)] z-40 w-72 p-1"
        >
          {children}
        </div>
      ) : null}
    </div>
  )
}

function MenuItem({
  onClick,
  hint,
  children,
}: {
  onClick: () => void
  hint: string
  children: ReactNode
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className="flex w-full flex-col items-start gap-0.5 rounded-sm px-2 py-1.5 text-left hover:bg-surface-2"
    >
      <span className="text-xs font-semibold text-fg-strong">{children}</span>
      <span className="text-2xs leading-snug text-fg-subtle">{hint}</span>
    </button>
  )
}

/* ------------------------------------------------------------------ *
 * Selected offset well — the rail's only other occupant
 * ------------------------------------------------------------------ */

function OffsetDetail({
  offset,
  isSelected,
  onToggle,
  onReplay,
  onEvidence,
  onClose,
}: {
  offset: OffsetWell
  isSelected: boolean
  onToggle: () => void
  onReplay: () => void
  onEvidence: () => void
  onClose: () => void
}) {
  const w = offset.well
  const sim = offset.similarity
  const eventList = useBounded(offset.relevant_events, 8, 12)
  return (
    <SectionCard
      title={w.name}
      description={`${w.id} · ${w.field} · block ${w.block}`}
      dense
      actions={
        <button type="button" className={ghostButtonClass} onClick={onClose}>
          Back to ranking
        </button>
      }
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={relevanceTone(offset.relevance_score)}>{offset.relevance_band} relevance</Badge>
        <Badge tone={wellStatusTone(w.status)}>{humanizeEnum(w.status)}</Badge>
      </div>

      <div className="mt-2">
        <KeyValueGrid
          columns={4}
          items={[
            { label: 'Distance', value: fmtKm(offset.distance_km), mono: true },
            {
              label: 'Relevance',
              value: fmtPercent(offset.relevance_score, 0),
              mono: true,
              tone: relevanceTone(offset.relevance_score),
            },
            { label: 'Events', value: fmtNumber(offset.event_count, 0), mono: true },
            { label: 'Documents', value: fmtNumber(offset.document_count, 0), mono: true },
          ]}
        />
      </div>

      {/* Explainability stays one click away: the rail's job is to identify the
          well and let you act on it, not to re-argue the score. */}
      <div className="mt-2.5">
        <WhyFactors
          factors={offset.factors}
          title="Why this offset is relevant"
          defaultOpen={false}
        />
      </div>

      <div className="mt-2.5 flex flex-wrap gap-1.5">
        <button type="button" className={actionButtonClass} onClick={onReplay}>
          <LineIcon name="replay" size={12} />
          View replay
        </button>
        <button type="button" className={ghostButtonClass} onClick={onToggle}>
          {isSelected ? 'Remove from comparison' : 'Add to comparison'}
        </button>
        <button type="button" className={ghostButtonClass} onClick={onEvidence}>
          <LineIcon name="evidence" size={13} />
          Evidence
        </button>
      </div>

      {/* ------------------------ reference, closed -------------------------- */}
      <div className="mt-3 flex flex-col gap-2">
        <Disclosure
          summary="Well record"
          badge={
            <span className="tnum text-2xs text-fg-subtle">
              {fmtMeters(offset.depth_range.min_md)} – {fmtMeters(offset.depth_range.max_md)} MD
            </span>
          }
        >
          <dl>
            <Field label="Well id" mono>
              {w.id}
            </Field>
            <Field label="Field / block">{`${w.field} · ${w.block}`}</Field>
            <Field label="Operator">{w.operator}</Field>
            <Field label="Well type">{w.well_type}</Field>
            <Field label="Formation at current depth">{w.current_formation?.name ?? '—'}</Field>
            <Field label="Offset depth range (MD)" mono>
              {`${fmtMeters(offset.depth_range.min_md)} – ${fmtMeters(offset.depth_range.max_md)}`}
            </Field>
            <Field label="Max TVD" mono>
              {fmtMeters(offset.depth_range.max_tvd)}
            </Field>
            <Field label="Offset count / events" mono>
              {`${fmtNumber(w.offset_well_count, 0)} / ${fmtNumber(w.relevant_event_count, 0)}`}
            </Field>
            <Field label="Spud date">{fmtDate(w.spud_date)}</Field>
            <Field label="Water depth" mono>
              {fmtMeters(w.water_depth_m)}
            </Field>
            <Field label="Position" mono>
              {`${w.latitude.toFixed(4)} N, ${w.longitude.toFixed(4)} E`}
            </Field>
            <Field label="Source coverage">{offset.source_availability.coverage}</Field>
          </dl>
        </Disclosure>

        <Disclosure summary="Similarity breakdown" defaultOpen={false}>
          <dl className="space-y-2">
            {(
              [
                ['Formation similarity', sim.formation_similarity],
                ['Depth similarity', sim.depth_similarity],
                ['Spatial proximity', sim.spatial_proximity],
                ['Event similarity', sim.event_similarity],
              ] as const
            ).map(([label, value]) => (
              <div key={label}>
                <div className="flex items-baseline justify-between text-2xs text-fg-muted">
                  <span>{label}</span>
                  <span className="tnum font-semibold text-fg-strong">{value.toFixed(2)}</span>
                </div>
                <div className="mt-1">
                  <Meter
                    value={value}
                    fillClass="bg-accent"
                    label={`${label}: ${value.toFixed(2)}`}
                  />
                </div>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-2xs leading-relaxed text-fg-subtle">
            Similarity components are returned by the engine. Relevance is their weighted sum
            using the weights in Settings — engine defaults, not a validated model.
          </p>
        </Disclosure>

        <Disclosure summary="Why relevant, in words">
          {offset.why_relevant.length === 0 ? (
            <p className="text-xs text-fg-subtle">
              The engine returned no prose reasons for this offset.
            </p>
          ) : (
            <ul className="list-disc space-y-1 pl-4 text-xs text-fg">
              {offset.why_relevant.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
        </Disclosure>

        <Disclosure
          summary="Relevant historical events"
          badge={<span className="tnum text-2xs text-fg-subtle">{offset.relevant_events.length}</span>}
        >
          {offset.relevant_events.length === 0 ? (
            <p className="text-xs text-fg-subtle">
              The Directorate publishes no per-wellbore event attribution for this offset, so no
              historical events match. Its 1,250-event corpus is attributed to 15/9-F-9A only.
            </p>
          ) : (
            <>
              <ul className="divide-y divide-line">
                {eventList.visible.map((ev) => (
                  <li key={ev.id} className="flex items-start gap-2 py-1.5">
                    <span
                      className="mt-1 h-2 w-2 shrink-0 rounded-sm"
                      style={{ backgroundColor: severityFillVar(ev.severity) }}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-medium text-fg-strong">{ev.event_label}</p>
                      <p className="tnum text-2xs text-fg-muted">
                        {`${ev.id} · TVD ${fmtMeters(ev.tvd)} · ${ev.formation || '—'}`}
                      </p>
                    </div>
                    <Badge tone={severityTone(ev.severity)}>{ev.severity}</Badge>
                  </li>
                ))}
              </ul>
              <SliceNote
                className="mt-2"
                shown={eventList.shown}
                total={eventList.total}
                noun="events"
                onMore={eventList.more}
              />
            </>
          )}
        </Disclosure>
      </div>
    </SectionCard>
  )
}

export default OffsetMap
