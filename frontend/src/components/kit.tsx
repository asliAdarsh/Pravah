import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { LineIcon } from './LineIcon'
import { BADGE_TONE, KeyStat, TONE_TEXT, cx, type Tone } from './primitives'

/**
 * The layout kit every screen is assembled from (docs/UX_CONTRACT.md §2/§3).
 *
 * One screen, one hero: `ScreenHeader` answers "what is this", the hero panel
 * answers the question, and everything supporting goes below — paired two per
 * row at most, or inside a `Disclosure`.
 */

/* ------------------------------------------------------------------ *
 * Screen frame
 * ------------------------------------------------------------------ */

export function ScreenHeader({
  title,
  subtitle,
  actions,
  meta,
}: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
  /** Small chips: counts, record ids, status. */
  meta?: ReactNode
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-base font-semibold tracking-tight text-fg-strong">{title}</h1>
        {subtitle ? (
          <p className="mt-0.5 max-w-4xl text-xs leading-relaxed text-fg-muted">{subtitle}</p>
        ) : null}
        {meta ? <div className="mt-1.5 flex flex-wrap items-center gap-1.5">{meta}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
    </header>
  )
}

/**
 * A titled block on the page. `title` may be a node so a screen can render its
 * own numbering ("3 · Relevance engine") without a second heading component.
 */
export function SectionCard({
  title,
  description,
  actions,
  children,
  dense,
  className,
}: {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  dense?: boolean
  className?: string
}) {
  return (
    <section className={cx('panel-surface flex min-w-0 flex-col', className)}>
      {(title || description || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-2 border-b border-line px-3 py-2">
          <div className="min-w-0">
            {title ? (
              <h2 className="text-sm font-semibold tracking-tight text-fg-strong">{title}</h2>
            ) : null}
            {description ? (
              <p className="mt-0.5 max-w-3xl text-xs leading-relaxed text-fg-muted">{description}</p>
            ) : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
        </header>
      )}
      <div className={cx('min-w-0 flex-1', dense ? 'p-2' : 'p-3')}>{children}</div>
    </section>
  )
}

/* ------------------------------------------------------------------ *
 * Readouts
 * ------------------------------------------------------------------ */

/** A row of `KeyStat`s separated by hairlines instead of boxes. */
export function StatRow({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cx(
        'grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line bg-line sm:grid-cols-3 lg:grid-cols-4',
        className,
      )}
    >
      {children}
    </div>
  )
}

const KV_COLUMNS: Record<2 | 3 | 4, string> = {
  2: 'grid-cols-1 sm:grid-cols-2',
  3: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3',
  4: 'grid-cols-2 lg:grid-cols-4',
}

/** Label / value pairs for reference data that does not deserve its own panel. */
export function KeyValueGrid({
  items,
  columns = 2,
  className,
}: {
  items: { label: string; value: ReactNode; tone?: Tone; mono?: boolean }[]
  columns?: 2 | 3 | 4
  className?: string
}) {
  return (
    <dl className={cx('grid gap-x-4 gap-y-2', KV_COLUMNS[columns], className)}>
      {items.map((item) => (
        <div key={item.label} className="min-w-0">
          <dt className="text-2xs font-semibold uppercase tracking-[0.06em] text-fg-muted">
            {item.label}
          </dt>
          <dd
            className={cx(
              'mt-0.5 break-words text-sm',
              item.mono && 'tnum',
              TONE_TEXT[item.tone ?? 'neutral'],
            )}
          >
            {item.value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

/* ------------------------------------------------------------------ *
 * Progressive disclosure
 * ------------------------------------------------------------------ */

const DISCLOSURE_TONE: Record<Tone, string> = {
  neutral: 'border-line',
  info: 'border-accent-line',
  success: 'border-ok-line',
  warning: 'border-warn-line',
  critical: 'border-crit-line',
  muted: 'border-line',
}

/**
 * Reference data, method notes and full records live in here, closed by
 * default. A real `<details>` so it is keyboard- and screen-reader-native; the
 * body is animated by transitioning `grid-template-rows`, which honours the
 * app's `prefers-reduced-motion` rule without any keyframes.
 */
export function Disclosure({
  summary,
  children,
  defaultOpen = false,
  tone = 'neutral',
  badge,
}: {
  summary: ReactNode
  children: ReactNode
  defaultOpen?: boolean
  tone?: Tone
  /** Count chip rendered at the end of the summary row. */
  badge?: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className={cx(
        'group min-w-0 overflow-hidden rounded-md border bg-surface-1',
        DISCLOSURE_TONE[tone],
      )}
    >
      <summary
        className={cx(
          'flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-left hover:bg-surface-2',
          '[&::-webkit-details-marker]:hidden',
        )}
      >
        <LineIcon
          name="chevron"
          size={13}
          className={cx('shrink-0 text-fg-muted transition-transform duration-150', open && 'rotate-90')}
        />
        <span className="min-w-0 flex-1 text-sm font-medium text-fg-strong">{summary}</span>
        {badge ? <span className="shrink-0">{badge}</span> : null}
      </summary>
      <div
        className="grid transition-[grid-template-rows] duration-200 ease-out"
        style={{ gridTemplateRows: open ? '1fr' : '0fr' }}
      >
        <div className="overflow-hidden">
          <div className="border-t border-line px-3 py-2.5">{children}</div>
        </div>
      </div>
    </details>
  )
}

/* ------------------------------------------------------------------ *
 * Emphasis
 * ------------------------------------------------------------------ */

const CALLOUT_ICON: Record<Tone, ReactNode> = {
  neutral: <LineIcon name="info" size={14} />,
  info: <LineIcon name="info" size={14} />,
  success: <LineIcon name="check" size={14} />,
  warning: <LineIcon name="warning" size={14} />,
  critical: <LineIcon name="warning" size={14} />,
  muted: <LineIcon name="info" size={14} />,
}

/** One short, self-contained statement that must not be missed. */
export function Callout({
  tone,
  title,
  children,
  icon,
}: {
  tone: Tone
  title?: ReactNode
  children: ReactNode
  icon?: ReactNode
}) {
  return (
    <div className={cx('flex gap-2 rounded-md border px-3 py-2', BADGE_TONE[tone])}>
      <span className="mt-px shrink-0">{icon ?? CALLOUT_ICON[tone]}</span>
      <div className="min-w-0 flex-1">
        {title ? <p className="text-xs font-semibold">{title}</p> : null}
        <div className={cx('text-xs leading-relaxed', title ? 'mt-0.5' : undefined)}>{children}</div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ *
 * Controls
 * ------------------------------------------------------------------ */

/** Labelled range input with the live value spelled out beside the label. */
export function ControlSlider({
  label,
  value,
  min,
  max,
  step,
  onChange,
  format,
  hint,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  onChange: (next: number) => void
  format?: (value: number) => string
  hint?: string
}) {
  const id = useId()
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2">
        <label
          htmlFor={id}
          className="text-2xs font-semibold uppercase tracking-[0.06em] text-fg-muted"
        >
          {label}
        </label>
        <span className="tnum shrink-0 text-2xs font-semibold text-fg-strong">
          {format ? format(value) : value}
        </span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="mt-1.5 w-full accent-accent"
      />
      {hint ? <p className="mt-0.5 text-2xs text-fg-muted">{hint}</p> : null}
    </div>
  )
}

/* ------------------------------------------------------------------ *
 * Choice
 * ------------------------------------------------------------------ */

/**
 * Radio-group semantics over a row of buttons: arrow keys, Home and End move
 * and select, exactly as a native radio group behaves. The selected option is
 * the accent surface; every segmented control in the app looks the same.
 */
export function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
  label,
  size = 'md',
}: {
  value: T
  options: { id: T; label: ReactNode; hint?: string }[]
  onChange: (next: T) => void
  label: string
  size?: 'sm' | 'md'
}) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([])

  function move(index: number) {
    const last = options.length - 1
    let next = index
    if (index === undefined) return
    if (next < 0) next = 0
    if (next > last) next = last
    onChange(options[next].id)
    buttons.current[next]?.focus()
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        event.preventDefault()
        move(index + 1)
        break
      case 'ArrowLeft':
      case 'ArrowUp':
        event.preventDefault()
        move(index - 1)
        break
      case 'Home':
        event.preventDefault()
        move(0)
        break
      case 'End':
        event.preventDefault()
        move(options.length - 1)
        break
      default:
        break
    }
  }

  const hasHints = options.some((option) => option.hint)

  return (
    <div className="min-w-0">
      <div
        role="radiogroup"
        aria-label={label}
        className="inline-flex flex-wrap gap-1 rounded-md border border-line bg-surface-2 p-1"
      >
        {options.map((option, index) => {
          const selected = option.id === value
          return (
            <button
              key={option.id}
              ref={(node) => {
                buttons.current[index] = node
              }}
              type="button"
              role="radio"
              aria-checked={selected}
              tabIndex={selected ? 0 : -1}
              onClick={() => onChange(option.id)}
              onKeyDown={(event) => onKeyDown(event, index)}
              title={option.hint}
              className={cx(
                'rounded-sm font-semibold tracking-[0.04em] whitespace-nowrap transition-colors',
                size === 'sm' ? 'h-6 px-2 text-2xs' : 'h-7 px-3 text-xs',
                selected
                  ? 'bg-accent text-accent-fg'
                  : 'text-fg-muted hover:bg-surface-3 hover:text-fg-strong',
              )}
            >
              {option.label}
            </button>
          )
        })}
      </div>
      {hasHints ? (
        <ul className="mt-1.5 space-y-0.5">
          {options.map((option) => (
            <li
              key={option.id}
              className={cx(
                'text-2xs leading-snug',
                option.id === value ? 'text-fg' : 'text-fg-muted',
              )}
            >
              {option.hint}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

export { KeyStat }
