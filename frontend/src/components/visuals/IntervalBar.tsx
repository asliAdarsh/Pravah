/**
 * Wilson 95% score interval, drawn as a labelled bar on a fixed 0–100% track.
 *
 * The three numbers the backend returns are shown as exactly three numbers:
 * the Wilson lower bound, the point estimate and the Wilson upper bound. The
 * interval is never widened, never rounded up to a friendlier figure, and it is
 * never called "accuracy" — it is the precision of a specific episode
 * definition, which the caller supplies as `caption`.
 */
import type { ReactNode } from 'react'

import { useElementWidth } from '@/lib/useElementWidth'
import { fmtPercent } from '@/lib/format'

const TRACK_H = 34
const MARKER_W = 2

export function IntervalBar({
  lower,
  centre,
  upper,
  scaleMax = 1,
  label,
  caption,
}: {
  lower: number
  centre: number
  upper: number
  /** Right edge of the track. 1 = a proportion from 0 to 100%. */
  scaleMax?: number
  label: string
  caption?: ReactNode
}) {
  const { ref, width } = useElementWidth<HTMLDivElement>(520)
  const trackW = Math.max(width, 200)
  const span = upper - lower

  return (
    <div ref={ref} className="min-w-0">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-2xs font-semibold uppercase tracking-[0.06em] text-fg-muted">
          {label}
        </span>
        <span className="tnum text-2xs text-fg-subtle">
          {fmtPercent(lower)} – {fmtPercent(upper)}
        </span>
      </div>

      <svg
        width={trackW}
        height={TRACK_H}
        role="img"
        aria-label={`${label}: ${fmtPercent(lower)} to ${fmtPercent(upper)}, point estimate ${fmtPercent(centre)}, width ${(span * 100).toFixed(1)} percentage points`}
      >
        <rect x={0} y={10} width={trackW} height={12} rx={2} fill="var(--surface-2)" />
        <rect
          x={0}
          y={10}
          width={(lower / scaleMax) * trackW}
          height={12}
          rx={2}
          fill="var(--surface-3)"
        />
        {/* The interval itself. */}
        <rect
          x={(lower / scaleMax) * trackW}
          y={8}
          width={Math.max((span / scaleMax) * trackW, 2)}
          height={16}
          fill="var(--accent-soft)"
          stroke="var(--accent-line)"
          strokeWidth={1}
        />
        {/* Point estimate. */}
        <rect
          x={(centre / scaleMax) * trackW - MARKER_W / 2}
          y={4}
          width={MARKER_W}
          height={24}
          fill="var(--accent)"
        />
        <text x={0} y={32} fontSize={9} fill="var(--fg-subtle)" className="tnum">
          0%
        </text>
        <text
          x={trackW}
          y={32}
          textAnchor="end"
          fontSize={9}
          fill="var(--fg-subtle)"
          className="tnum"
        >
          {fmtPercent(scaleMax, 0)}
        </text>
      </svg>

      <p className="mt-1 text-2xs leading-relaxed text-fg-muted">{caption}</p>
    </div>
  )
}
