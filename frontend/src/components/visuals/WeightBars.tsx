/**
 * AHP feature weights as a horizontal bar chart, plus the consistency ratio
 * against Saaty's 0.10 threshold.
 *
 * Bars are scaled to the largest weight in the set rather than to 100%, so the
 * ranking between features is legible at a glance, and the exact percentage is
 * printed on every bar. The consistency ratio is shown against the threshold it
 * is judged by, with the threshold drawn as a rule on the same scale — a bare
 * number invites the reader to supply their own idea of "small".
 */
import { cx } from '@/components/ui'
import { fmtNumber } from '@/lib/format'

/** Saaty's random-index consistency threshold. The backend sends the same value. */
export const SAATY_CR_THRESHOLD = 0.1

export function WeightBars({
  features,
  weights,
  className,
}: {
  features: string[]
  weights: Record<string, number>
  className?: string
}) {
  const rows = features.map((feature) => ({ feature, weight: weights[feature] ?? 0 }))
  const max = Math.max(...rows.map((row) => row.weight), 0.0001)

  return (
    <ul className={cx('flex flex-col gap-1.5', className)}>
      {rows.map((row) => (
        <li key={row.feature} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
          <div className="min-w-0">
            <p className="truncate text-2xs font-medium text-fg-muted">
              {row.feature.replace(/_/g, ' ')}
            </p>
            <div className="mt-0.5 h-2.5 overflow-hidden rounded-sm bg-surface-2">
              <div
                className="h-full rounded-sm bg-accent"
                style={{ width: `${(row.weight / max) * 100}%` }}
              />
            </div>
          </div>
          <span className="tnum w-14 shrink-0 text-right text-sm font-semibold text-fg-strong">
            {fmtNumber(row.weight * 100, 1)}%
          </span>
        </li>
      ))}
    </ul>
  )
}

/**
 * Consistency ratio against the Saaty threshold, on a 0–0.2 scale with the
 * 0.10 line drawn in. Below the line is the acceptable region.
 */
export function ConsistencyMeter({
  ratio,
  threshold,
  lambdaMax,
  criteriaCount,
  method,
}: {
  ratio: number
  threshold: number
  lambdaMax: number
  criteriaCount: number
  method: string
}) {
  const scaleMax = Math.max(threshold * 2, ratio * 1.15, 0.01)
  const pct = (value: number) => `${Math.min(100, (value / scaleMax) * 100)}%`
  const consistent = ratio <= threshold

  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="text-2xs font-semibold uppercase tracking-[0.06em] text-fg-muted">
          Consistency ratio
        </span>
        <span
          className={cx(
            'tnum text-sm font-semibold',
            consistent ? 'text-ok' : 'text-warn',
          )}
        >
          {fmtNumber(ratio, 4)}
        </span>
      </div>

      <div className="relative mt-1 h-4 rounded-sm bg-surface-2">
        <div
          className={cx('h-full rounded-sm', consistent ? 'bg-ok-soft' : 'bg-warn-soft')}
          style={{ width: pct(threshold) }}
        />
        <div
          className={cx('absolute inset-y-0 w-0.5', consistent ? 'bg-ok' : 'bg-warn')}
          style={{ left: pct(ratio) }}
        />
        <div
          className="absolute inset-y-0 w-px bg-fg-subtle"
          style={{ left: pct(threshold) }}
          title={`Saaty threshold ${threshold}`}
        />
      </div>

      <p className="mt-1 text-2xs leading-relaxed text-fg-muted">
        {fmtNumber(ratio, 4)} against Saaty's {threshold} threshold for {criteriaCount} criteria
        (λ<sub>max</sub> {fmtNumber(lambdaMax, 3)}), computed by {method.replace(/_/g, ' ').toLowerCase()}.
        {consistent
          ? ' The judgements are internally consistent.'
          : ' The judgements are not consistent enough to trust as stated — re-check the pairwise comparisons.'}
      </p>
    </div>
  )
}
