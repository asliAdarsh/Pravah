/**
 * Screen-layer helpers shared by the route components in this folder.
 *
 * Scope note: this file lives in `src/pages/` on purpose. It is not a route and
 * exports no default component. It deliberately does NOT re-implement anything
 * the UI kit already provides (headers, cards, disclosures, buttons, controls,
 * meters, provenance tags, fetch state) — only the handful of screen-local
 * pieces the kit does not cover.
 *
 * Colour rule (UX contract §1): every class in this file is a semantic token
 * (`surface-*` / `line*` / `fg-*` / `accent*` / `ok-*` / `warn-*` / `crit-*`).
 * The raw `ink-*` / `navy-*` / `brand-*` palette is for chart fills only (§6).
 */
import type { ReactNode } from 'react'
import type { EventSeverity } from '@/api/types'
import { LineIcon, ScreenHeader as KitScreenHeader, Skeleton, cx } from '@/components/ui'

/* ------------------------------------------------------------------ *
 * Numbers
 * ------------------------------------------------------------------ */

export function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min
  return Math.min(max, Math.max(min, value))
}

/** "Nice" axis step (1 / 2 / 2.5 / 5 × 10ⁿ) so rulers never show 0.37 m intervals. */
export function niceStep(raw: number): number {
  if (!Number.isFinite(raw) || raw <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const f = raw / magnitude
  const mult = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10
  return mult * magnitude
}

/** Evenly spaced tick values covering [min, max]. */
export function ticksBetween(min: number, max: number, step: number): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min || step <= 0) return []
  const out: number[] = []
  for (let v = Math.ceil(min / step) * step; v <= max + step * 0.001; v += step) {
    out.push(Number(v.toFixed(6)))
  }
  return out
}

/** Pixel offset of a depth inside a [top, bottom] window, as a CSS percentage. */
export function depthPercent(depth: number, top: number, bottom: number): number {
  const span = bottom - top
  if (!Number.isFinite(span) || span <= 0) return 0
  return ((depth - top) / span) * 100
}

/* ------------------------------------------------------------------ *
 * Chart colours (UX contract §6)
 *
 * These return CSS colour *values*, not class names: the screens use them for
 * inline SVG `fill` / `stroke` and for `style.backgroundColor`, where a Tailwind
 * class cannot reach. Mapping to the themed band tokens is what keeps the depth
 * plots, the location plan and the replay lanes legible in dark mode.
 * ------------------------------------------------------------------ */

/** Event severity → band token. Red is reserved for CRITICAL alone. */
export function severityFillVar(severity: EventSeverity | null | undefined): string {
  switch (severity) {
    case 'CRITICAL':
      return 'var(--band-critical)'
    case 'HIGH':
      return 'var(--band-high)'
    case 'MODERATE':
      return 'var(--band-warning)'
    case 'LOW':
      return 'var(--ok)'
    default:
      return 'var(--fg-subtle)'
  }
}

/**
 * Relevance score → band token, using the engine's own cut-offs
 * (HIGH 0.70 / MEDIUM 0.45 / LOW 0.25) so the marker colour and the returned
 * `relevance_band` always agree.
 *
 * The top band deliberately uses `--band-high` (amber), not `--band-critical`:
 * product rule "red is reserved for critical" means a *relevance* score must
 * never paint red, however high it is. Only alert/event severity may.
 */
export function relevanceFillVar(score: number | null | undefined): string {
  if (score === null || score === undefined || !Number.isFinite(score)) return 'var(--fg-subtle)'
  if (score >= 0.7) return 'var(--band-high)'
  if (score >= 0.45) return 'var(--band-warning)'
  if (score >= 0.25) return 'var(--band-info)'
  return 'var(--fg-subtle)'
}

/* ------------------------------------------------------------------ *
 * Page chrome
 * ------------------------------------------------------------------ */

/** Form-control label style shared by the filter toolbars. */
export const LABEL = 'block text-2xs font-medium uppercase tracking-wider text-fg-muted'

/** Contract rule #1 — every data surface carries this label. */
export function SyntheticStrip({ label }: { label?: string | null }) {
  return (
    <span className="prov-strip inline-flex items-center gap-1.5 rounded-sm px-2 py-1 text-2xs font-semibold uppercase tracking-wider text-warn">
      <LineIcon name="warning" size={12} />
      {label ?? 'Real public data'}
    </span>
  )
}
/**
 * Screen title block. The one implementation is the kit `ScreenHeader`; this is
 * a prop-compat adapter, not a second design. `datasetLabel` is the legacy chip
 * spelling kept only so the sibling screens in this folder keep compiling, and
 * it resolves to a `SyntheticStrip` in the `meta` slot — so this collapses to a
 * plain `export { ScreenHeader }` once they are off the old prop.
 */
export function ScreenHeader({
  title,
  subtitle,
  datasetLabel,
  actions,
  meta,
}: {
  title: string
  subtitle?: ReactNode
  datasetLabel?: string | null
  actions?: ReactNode
  meta?: ReactNode
}) {
  return (
    <KitScreenHeader
      title={title}
      subtitle={subtitle}
      actions={actions}
      meta={meta ?? (datasetLabel === undefined ? undefined : <SyntheticStrip label={datasetLabel} />)}
    />
  )
}

export function ErrorPanel({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-md border border-crit-line bg-crit-soft px-3 py-3">
      <p className="flex items-center gap-1.5 text-sm font-semibold text-crit">
        <LineIcon name="warning" size={14} />
        Data unavailable
      </p>
      <p className="text-xs leading-relaxed text-crit">{message}</p>
      {onRetry ? (
        <button type="button" className="ghostButtonClass" onClick={onRetry}>
          Retry request
        </button>
      ) : null}
    </div>
  )
}

export function ResourceState({
  loading,
  error,
  onRetry,
  skeleton,
  children,
}: {
  loading: boolean
  error: string | null
  onRetry?: () => void
  skeleton?: ReactNode
  children: ReactNode
}) {
  if (loading) {
    return (
      <div className="p-3">
        {skeleton ?? (
          <div className="space-y-2">
            <Skeleton className="h-3 w-1/3" />
            <Skeleton className="h-40 w-full" />
          </div>
        )}
      </div>
    )
  }
  if (error) return <ErrorPanel message={error} onRetry={onRetry} />
  return <>{children}</>
}

/** Definition-list row used by every detail panel on the screens. */
export function Field({
  label,
  children,
  mono,
  className,
}: {
  label: string
  children: ReactNode
  mono?: boolean
  className?: string
}) {
  return (
    <div
      className={cx(
        'flex items-baseline justify-between gap-3 border-b border-line py-1 last:border-b-0',
        className,
      )}
    >
      <dt className="shrink-0 text-2xs font-medium uppercase tracking-wider text-fg-muted">
        {label}
      </dt>
      <dd className={cx('min-w-0 text-right text-xs text-fg', mono && 'tnum')}>{children}</dd>
    </div>
  )
}

/**
 * Plot legend entry. `color` takes a CSS colour value (see `severityFillVar` /
 * `relevanceFillVar`); `className` stays available for border-only swatches.
 */
export function LegendSwatch({
  className,
  color,
  label,
}: {
  className?: string
  color?: string
  label: string
}) {
  return (
    <span className="inline-flex items-center gap-1.5 text-2xs text-fg-muted">
      <span
        className={cx('h-2.5 w-2.5 shrink-0 rounded-sm', !color && 'bg-fg-subtle', className)}
        style={color ? { backgroundColor: color } : undefined}
      />
      {label}
    </span>
  )
}

