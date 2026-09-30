import { cx } from './primitives'

/**
 * Vertical depth ruler with gridline ticks.
 *
 * Standalone component (not a track you nest content into): render it as a
 * sibling of the plot area with the same pixel height and the ticks line up.
 * Reused by offset replay (TVD column) and the depth-aware timeline (MD column).
 *
 * It owns a default height so a call site never has to repeat one. Any `h-*`
 * utility in `className` wins over the default: two arbitrary-value height
 * utilities in one class list would otherwise be resolved by stylesheet order
 * rather than by the call site.
 */
const DEFAULT_AXIS_HEIGHT = 'h-[520px]'

/** A caller-supplied `h-*` utility replaces the default height entirely. */
const HEIGHT_OVERRIDE = /(?:^|\s)h-/

export function DepthAxis({
  topM,
  bottomM,
  className,
  labels,
}: {
  topM: number
  bottomM: number
  className?: string
  labels?: number[]
}) {
  const axisClass = HEIGHT_OVERRIDE.test(className ?? '')
    ? className
    : cx(DEFAULT_AXIS_HEIGHT, className)
  const span = bottomM - topM

  if (!Number.isFinite(span) || span <= 0) {
    return (
      <div className={cx('w-14 shrink-0 text-2xs text-fg-muted', axisClass)}>
        <span className="tnum">—</span>
      </div>
    )
  }

  const ticks = labels ?? defaultTicks(topM, bottomM)

  return (
    <div className={cx('relative w-14 shrink-0 select-none', axisClass)} aria-hidden>
      {ticks.map((depth) => {
        const ratio = (depth - topM) / span
        if (ratio < -0.001 || ratio > 1.001) return null
        const top = `${(ratio * 100).toFixed(3)}%`
        return (
          <div key={depth} className="absolute inset-x-0" style={{ top }}>
            <div className="flex items-center justify-end gap-1 pr-1">
              <span className="h-px w-2 bg-line-strong" />
              <span className="tnum text-2xs text-fg-muted">{Math.round(depth).toLocaleString('en-US')}</span>
            </div>
            <div className="absolute top-0 -left-px h-px w-px bg-line-strong" />
          </div>
        )
      })}
      <div className="absolute inset-y-0 right-0 w-px bg-line" />
    </div>
  )
}

/** Nice 1/2/5 step selection so labels stay round numbers at any span. */
function defaultTicks(topM: number, bottomM: number): number[] {
  const span = bottomM - topM
  const rough = span / 8
  const magnitude = 10 ** Math.floor(Math.log10(rough))
  const candidates = [1, 2, 2.5, 5, 10].map((factor) => factor * magnitude)
  const step = candidates.find((candidate) => candidate >= rough) ?? magnitude * 10

  const ticks: number[] = []
  const first = Math.ceil(topM / step) * step
  for (let value = first; value <= bottomM + 1e-6; value += step) {
    ticks.push(Math.round(value))
  }
  return ticks
}
