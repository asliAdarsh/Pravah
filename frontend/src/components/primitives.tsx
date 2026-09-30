import type { ReactNode } from 'react'

/** Semantic tone shared by Badge / KeyStat / status chips. */
export type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'critical' | 'muted'

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}

/* Static class maps — Tailwind v4 cannot resolve interpolated class names.
 * Every entry is a semantic token (docs/UX_CONTRACT.md §1); the raw ink/navy/
 * brand palette must never appear here or dark mode breaks. */

export const BADGE_TONE: Record<Tone, string> = {
  neutral: 'bg-surface-2 text-fg border-line-strong',
  info: 'bg-accent-soft text-accent border-accent-line',
  success: 'bg-ok-soft text-ok border-ok-line',
  warning: 'bg-warn-soft text-warn border-warn-line',
  critical: 'bg-crit-soft text-crit border-crit-line',
  muted: 'bg-surface-1 text-fg-muted border-line',
}

export const BADGE_DOT: Record<Tone, string> = {
  neutral: 'bg-fg-muted',
  info: 'bg-accent',
  success: 'bg-ok',
  warning: 'bg-warn',
  critical: 'bg-crit',
  muted: 'bg-fg-muted',
}

/** Text-only tone ramp for numeric readouts (KeyStat values, KV grid values). */
export const TONE_TEXT: Record<Tone, string> = {
  neutral: 'text-fg-strong',
  info: 'text-accent',
  success: 'text-ok',
  warning: 'text-warn',
  critical: 'text-crit',
  muted: 'text-fg-muted',
}

/** First `limit` distinct entries, preserving the order the engine returned. */
export function uniqueReasons(values: readonly string[], limit: number): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const value of values) {
    if (seen.has(value)) continue
    seen.add(value)
    out.push(value)
    if (out.length === limit) break
  }
  return out
}

const ALIGN_CLASS: Record<'left' | 'right' | 'center', string> = {
  left: 'text-left',
  right: 'text-right',
  center: 'text-center',
}

/* ------------------------------------------------------------------ *
 * Layout
 * ------------------------------------------------------------------ */

/** The primary labelled readout — a label, one number, an optional unit and hint. */
export function KeyStat({
  label,
  value,
  unit,
  hint,
  tone = 'neutral',
  className,
}: {
  label: string
  value: ReactNode
  unit?: string
  hint?: string
  tone?: Tone
  className?: string
}) {
  return (
    <div className={cx('flex min-w-0 flex-col gap-0.5 bg-surface-1 px-3 py-2', className)}>
      <span className="truncate text-2xs font-semibold uppercase tracking-[0.07em] text-fg-muted">
        {label}
      </span>
      <span className={cx('tnum text-lg leading-tight font-semibold', TONE_TEXT[tone])}>
        {value}
        {unit && <span className="ml-1 text-xs font-normal text-fg-muted">{unit}</span>}
      </span>
      {hint && <span className="truncate text-2xs text-fg-muted">{hint}</span>}
    </div>
  )
}

/** Same component under the name the existing screens already import. */
export const Metric = KeyStat


export function Card({
  title,
  subtitle,
  actions,
  children,
  className,
  dense,
}: {
  title?: string
  subtitle?: string
  actions?: ReactNode
  children: ReactNode
  className?: string
  dense?: boolean
}) {
  return (
    <section className={cx('panel-surface flex min-w-0 flex-col', className)}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-line px-3 py-2">
          <div className="min-w-0">
            {title && (
              <h2 className="truncate text-sm font-semibold tracking-tight text-fg-strong">{title}</h2>
            )}
            {subtitle && <p className="mt-0.5 text-xs text-fg-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div className={cx('min-w-0 flex-1', dense ? 'p-2' : undefined)}>{children}</div>
    </section>
  )
}

export function Panel({
  title,
  children,
  className,
  dense,
  actions,
}: {
  title: string
  children: ReactNode
  className?: string
  dense?: boolean
  actions?: ReactNode
}) {
  return (
    <section className={cx('panel-surface min-w-0', className)}>
      <header className="flex items-center justify-between gap-2 border-b border-line px-3 py-1.5">
        <h3 className="truncate text-xs font-semibold uppercase tracking-[0.06em] text-fg-strong">
          {title}
        </h3>
        {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
      </header>
      <div className={dense ? 'px-3 py-2' : 'px-3 py-3'}>{children}</div>
    </section>
  )
}

export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cx(
        'flex flex-wrap items-center gap-2 border-b border-line bg-surface-1 px-3 py-2',
        className,
      )}
    >
      {children}
    </div>
  )
}

export function SectionTitle({
  children,
  hint,
  actions,
}: {
  children: ReactNode
  hint?: string
  actions?: ReactNode
}) {
  return (
    <div className="flex items-end justify-between gap-3 border-b border-line pb-1.5">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold uppercase tracking-[0.06em] text-fg-strong">{children}</h2>
        {hint && <p className="mt-0.5 text-xs text-fg-muted">{hint}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
    </div>
  )
}

/* ------------------------------------------------------------------ *
 * Data display
 * ------------------------------------------------------------------ */

export function Badge({
  tone = 'neutral',
  children,
  icon,
}: {
  tone?: Tone
  children: ReactNode
  icon?: ReactNode
}) {
  return (
    <span
      className={cx(
        'inline-flex max-w-full items-center gap-1 rounded-sm border px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-[0.05em] whitespace-nowrap',
        BADGE_TONE[tone],
      )}
    >
      {icon ?? <span className={cx('size-1.5 shrink-0 rounded-full', BADGE_DOT[tone])} />}
      <span className="truncate">{children}</span>
    </span>
  )
}

export interface DataTableColumn<T> {
  key: string
  header: string
  width?: string | number
  align?: 'left' | 'right' | 'center'
  render?: (row: T, index: number) => ReactNode
}

export interface DataTableProps<T> {
  columns: DataTableColumn<T>[]
  rows: T[]
  rowKey: (row: T, index: number) => string
  onRowClick?: (row: T, index: number) => void
  selectedKey?: string
  empty?: ReactNode
  className?: string
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  selectedKey,
  empty,
  className,
}: DataTableProps<T>) {
  if (rows.length === 0) {
    return <div className={cx('p-3', className)}>{empty ?? <EmptyState title="No records" />}</div>
  }

  return (
    <div className={cx('scroll-thin w-full overflow-x-auto', className)}>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-line bg-surface-2 text-2xs uppercase tracking-[0.07em] text-fg-muted">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                style={column.width === undefined ? undefined : { width: column.width }}
                className={cx(
                  'px-2.5 py-1.5 font-semibold',
                  ALIGN_CLASS[column.align ?? 'left'],
                )}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const key = rowKey(row, index)
            const selected = selectedKey !== undefined && key === selectedKey
            return (
              <tr
                key={key}
                onClick={onRowClick ? () => onRowClick(row, index) : undefined}
                className={cx(
                  'border-b border-line last:border-b-0',
                  onRowClick && 'cursor-pointer hover:bg-surface-3',
                  selected && 'bg-accent-soft',
                )}
              >
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={cx('px-2.5 py-1.5 align-middle', ALIGN_CLASS[column.align ?? 'left'])}
                  >
                    {column.render
                      ? column.render(row, index)
                      : String((row as Record<string, unknown>)[column.key] ?? '—')}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function EmptyState({
  title,
  hint,
  icon,
}: {
  title: string
  hint?: string
  icon?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-1.5 px-4 py-8 text-center">
      {icon && <div className="text-fg-muted">{icon}</div>}
      <p className="text-sm font-semibold text-fg-strong">{title}</p>
      {hint && <p className="max-w-md text-xs text-fg-muted">{hint}</p>}
    </div>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div aria-hidden className={cx('skeleton-block animate-pulse rounded-sm', className ?? 'h-3 w-full')} />
  )
}

/** Thin inline meter used for relevance / risk / contribution readouts. */
export function Meter({
  value,
  fillClass,
  height = 6,
  label,
}: {
  value: number
  fillClass: string
  height?: number
  label?: string
}) {
  const pct = Math.max(0, Math.min(1, value)) * 100
  return (
    <div
      className="w-full overflow-hidden rounded-sm bg-surface-3"
      style={{ height }}
      role="img"
      aria-label={label ?? `${Math.round(pct)}%`}
    >
      <div className={cx('h-full rounded-sm', fillClass)} style={{ width: `${pct}%` }} />
    </div>
  )
}

/* ------------------------------------------------------------------ *
 * Controls
 * ------------------------------------------------------------------ */

/** Standard small control (buttons, selects, inputs) — restrained, industrial. */
export const controlClass =
  'h-7 rounded-sm border border-line-strong bg-surface-1 px-2 text-xs text-fg shadow-[var(--shadow-panel)] disabled:cursor-not-allowed disabled:opacity-55'

export const actionButtonClass =
  'inline-flex h-7 items-center gap-1.5 rounded-sm border border-accent bg-accent px-2.5 text-2xs font-semibold uppercase tracking-[0.06em] text-accent-fg hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-55'

export const ghostButtonClass =
  'inline-flex h-7 items-center gap-1.5 rounded-sm border border-line-strong bg-surface-1 px-2.5 text-2xs font-semibold uppercase tracking-[0.06em] text-fg-muted hover:border-accent-line hover:text-fg-strong disabled:cursor-not-allowed disabled:opacity-55'
