import { useState } from 'react'
import type { Factor } from '@/api/types'
import { LineIcon } from './LineIcon'
import { Meter, cx } from './primitives'
import { fmtNumber, fmtPercent } from '@/lib/format'

/**
 * The ONLY sanctioned renderer of API `factors[]` (contract §0.3).
 *
 * Renders `code, label, value, weight, contribution, detail` verbatim — the UI
 * never re-derives relevance or risk math. Used for "WHY THIS ALERT",
 * "WHY RELEVANT" on offset wells, and risk factors on the decision panel.
 */
export function WhyFactors({
  factors,
  title = 'Why this',
  defaultOpen = true,
}: {
  factors: Factor[]
  title?: string
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)

  if (factors.length === 0) {
    return (
      <div className="px-3 py-2 text-xs text-fg-muted">
        No explainability factors returned by the engine for this record.
      </div>
    )
  }

  const maxContribution = factors.reduce((max, factor) => Math.max(max, factor.contribution), 0)

  return (
    <div className="min-w-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 border-b border-line bg-surface-2 px-3 py-1.5 text-left hover:bg-surface-3"
      >
        <span className="text-2xs font-semibold uppercase tracking-[0.08em] text-fg-strong">
          {title}
          <span className="ml-1.5 font-normal normal-case tracking-normal text-fg-muted">
            {factors.length} factor{factors.length === 1 ? '' : 's'} · engine-supplied
          </span>
        </span>
        <LineIcon
          name="chevron"
          size={12}
          className={cx('shrink-0 text-fg-muted transition-transform', open && 'rotate-90')}
        />
      </button>

      {open && (
        <ul className="divide-y divide-line">
          {factors.map((factor) => (
            <li key={`${factor.code}-${factor.label}`} className="px-3 py-2">
              <div className="flex items-baseline justify-between gap-3">
                <span className="truncate text-sm font-medium text-fg-strong">{factor.label}</span>
                <span className="tnum shrink-0 text-xs text-fg-muted">
                  value {fmtNumber(factor.value, 2)}
                </span>
              </div>

              <div className="mt-1.5 flex items-center gap-2">
                <Meter
                  value={maxContribution > 0 ? factor.contribution / maxContribution : 0}
                  // A factor's colour would imply a severity the API never
                  // assigned; the bar length and the numbers carry the weight.
                  fillClass="bg-accent"
                  height={5}
                  label={`${factor.label} contribution`}
                />
                <span className="tnum shrink-0 text-2xs text-fg-muted">
                  w {fmtNumber(factor.weight, 2)} · c {fmtNumber(factor.contribution, 3)}
                </span>
              </div>

              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="rounded-sm border border-line bg-surface-2 px-1 py-px text-2xs text-fg-muted">
                  {factor.code}
                </span>
                <span className="text-2xs text-fg-muted">share of score {fmtPercent(factor.contribution)}</span>
              </div>

              {factor.detail && (
                <p className="mt-1 text-xs leading-snug text-fg-muted">{factor.detail}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
