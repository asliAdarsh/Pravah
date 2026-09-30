/**
 * The two "calm" rules this corpus forces on every screen, in one place.
 *
 * `docs/UX_CONTRACT.md` §2 sets the layout skeleton. The real public dataset
 * (NPD FactPages + Equinor Volve, 119 wells / 1,250 events / 203 documents)
 * breaks two assumptions the earlier synthetic-data pass never had to express,
 * so both need one shared, non-negotiable rendering:
 *
 *  1. **Derived data must look derived.** `15/9-F-9A` is a live well with no
 *     published formation rows and no published coordinate row. Its
 *     stratigraphic column is a labelled 15/9-block proxy and its position is
 *     the mean UTM of the five published 15/9 block wells. The API states this
 *     in `operating_context.status_note`; these helpers read that sentence so
 *     the marker can never drift from the data.
 *  2. **An empty field is not a defect.** Alerts are raised by
 *     `RULE_TELEMETRY_ANOMALY` on live MWD / mud-logger telemetry, so
 *     `supporting_well_count` is legitimately `0` — the Directorate publishes
 *     no per-wellbore DDR attribution. "0 supporting wells" must never be
 *     rendered as though something were missing.
 *
 * Plus the bounded-list control every screen needs now that no table may render
 * an unbounded result set.
 */
import { useCallback, useMemo, useState, type ReactNode } from 'react'
import type { AlertBrief, Well } from '@/api/types'
import { LineIcon, cx } from '@/components/ui'

/* ------------------------------------------------------------------ *
 * 1 · Derived vs published
 * ------------------------------------------------------------------ */

/** Why the active well's stratigraphic column is not a published record. */
export const PROXY_FORMATION_REASON =
  'Labelled 15/9-block proxy. The Norwegian Petroleum Directorate publishes no formation rows for this wellbore, so the column is derived from the published 15/9 block tops.'

/** Why the active well's position is not a published coordinate. */
export const PROXY_POSITION_REASON =
  'Position is the mean published UTM of the five 15/9 block wells; the Directorate publishes no coordinate row for this wellbore.'

/** The telemetry rule id — alerts carrying it are evidenced by live sensors. */
const TELEMETRY_RULE_ID = 'RULE_TELEMETRY_ANOMALY'

/** True when this well's stratigraphy is derived rather than published. */
export function isProxyFormation(well: Well | null | undefined): boolean {
  return /labelled proxy/i.test(well?.operating_context?.status_note ?? '')
}

/** True when this well's coordinates are derived rather than published. */
export function isProxyPosition(well: Well | null | undefined): boolean {
  return /mean published UTM/i.test(well?.operating_context?.status_note ?? '')
}

/** A quiet footnote chip. Never a warning tone — a proxy is labelled, not wrong. */
export function ProxyTag({ children = 'Proxy' }: { children?: ReactNode }) {
  return (
    <span className="inline-flex shrink-0 items-center rounded-sm border border-line bg-surface-2 px-1.5 py-px text-2xs font-semibold uppercase tracking-[0.05em] text-fg-muted">
      {children}
    </span>
  )
}

/**
 * A formation name carrying the proxy marker, for every place the active well's
 * formation is shown. `well` is the full record — the only payload that carries
 * the note — so pass `null` while it loads and the name simply stands alone.
 */
export function FormationName({
  well,
  name,
  className,
}: {
  well: Well | null | undefined
  name?: string | null
  className?: string
}) {
  const formation = name ?? well?.current_formation.name ?? '—'
  if (!isProxyFormation(well)) return <span className={className}>{formation}</span>
  return (
    <span className={cx('inline-flex flex-wrap items-center gap-1.5', className)}>
      <span>{formation}</span>
      <ProxyTag>15/9 proxy</ProxyTag>
    </span>
  )
}

/* ------------------------------------------------------------------ *
 * 2 · Alert evidence source
 * ------------------------------------------------------------------ */

/** True when the alert is evidenced by live telemetry rather than offset history. */
export function isTelemetryAlert(alert: AlertBrief): boolean {
  return alert.rule_id === TELEMETRY_RULE_ID
}

/**
 * The evidence line for one alert. Telemetry-sourced alerts name what actually
 * backs them; offset-attribute alerts keep the well/event counts they have.
 */
export function AlertEvidence({ alert, className }: { alert: AlertBrief; className?: string }) {
  if (isTelemetryAlert(alert)) {
    return (
      <span className={cx('tnum text-2xs text-fg-muted', className)}>
        {`Live telemetry · ${alert.supporting_event_count} alarm observation${
          alert.supporting_event_count === 1 ? '' : 's'
        } · no per-wellbore DDR attribution`}
      </span>
    )
  }
  return (
    <span className={cx('tnum text-2xs text-fg-muted', className)}>
      {`${alert.supporting_well_count} supporting well${
        alert.supporting_well_count === 1 ? '' : 's'
      } · ${alert.supporting_event_count} events`}
    </span>
  )
}

/* ------------------------------------------------------------------ *
 * 3 · Bounded lists
 * ------------------------------------------------------------------ */

/**
 * Slice a result set to a readable default with a deliberate "show more".
 * `step` grows geometrically so a 1,250-row corpus never needs twelve clicks.
 */
export function useBounded<T>(rows: readonly T[], initial = 12, step = 4) {
  const [limit, setLimit] = useState(initial)
  const visible = useMemo(() => rows.slice(0, limit), [rows, limit])
  const more = useCallback(() => setLimit((n) => n + step), [step])
  const reset = useCallback(() => setLimit(initial), [initial])
  return { visible, shown: visible.length, total: rows.length, more, reset }
}

/**
 * The "showing N of M" footer every bounded table and list carries. Renders
 * nothing at all when the whole set already fits, so small screens stay clean.
 */
export function SliceNote({
  shown,
  total,
  noun = 'records',
  onMore,
  className,
}: {
  shown: number
  total: number
  noun?: string
  onMore: () => void
  className?: string
}) {
  if (total <= shown) {
    return (
      <p className={cx('text-2xs text-fg-subtle', className)}>
        {`Showing all ${total} ${noun}.`}
      </p>
    )
  }
  return (
    <div
      className={cx(
        'flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2',
        className,
      )}
    >
      <p className="tnum text-2xs text-fg-subtle">
        {`Showing ${shown} of ${total} ${noun}.`}
      </p>
      <button
        type="button"
        onClick={onMore}
        className="inline-flex h-6 items-center gap-1.5 rounded-sm border border-line-strong bg-surface-1 px-2 text-2xs font-semibold uppercase tracking-[0.06em] text-fg-muted transition-colors hover:border-accent-line hover:text-fg-strong"
      >
        <LineIcon name="chevron" size={11} />
        {`Show ${Math.min(total - shown, stepOf(shown))} more`}
      </button>
    </div>
  )
}

/** The next sensible jump for "show more": the current slice size, floor 10. */
function stepOf(shown: number): number {
  return Math.max(10, shown)
}
