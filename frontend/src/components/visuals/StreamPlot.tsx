/**
 * Depth-indexed telemetry stream plot — one measured-depth axis, one lane per
 * selected channel.
 *
 * The rule this component exists to enforce: **a gap is drawn as a gap.** A row
 * where a channel has no reading produces no point, and a lane is broken at
 * every such row, so a polyline never crosses an unmeasured interval. Where the
 * break is wide enough to matter, the interval is shaded and captioned "no
 * measurement logged in this interval" — never interpolated, never
 * forward-filled, never bridged.
 *
 * Channels get separate lanes rather than a shared value axis because their
 * units differ (kkgf, rpm, g/cm3, m/h). One axis would be a lie; a normalised
 * overlay would be unreadable.
 */
import { useMemo, useState, type MouseEvent } from 'react'
import { DepthAxis, cx } from '@/components/ui'
import { fmtMeters, fmtNumber } from '@/lib/format'
import { useElementWidth } from '@/lib/useElementWidth'
import { niceStep, ticksBetween } from '../../pages/_shared'
import type { TelemetryStreamPoint } from '../../pages/_engines'

export interface StreamSeries {
  channel: string
  unit: string
  color: string
}

const LANE_H = 96
const LANE_GAP = 12
const HEADER_H = 26
const VALUE_AXIS_W = 48
const PAD_RIGHT = 46
const AXIS_W = 56
/** A break narrower than this (in metres of hole) is an ordinary sampling step. */
const MIN_GAP_LABEL_MD = 12

interface Coverage {
  /** First and last depth at which this channel was genuinely measured. */
  firstMd: number
  lastMd: number
  /** Real readings in the whole log, before downsampling. */
  samples: number
}

interface Lane {
  series: StreamSeries
  /** Contiguous measured runs: one polyline is drawn per run. */
  runs: { md: number; value: number }[][]
  /**
   * Depths where the channel has no reading at all, from the channel
   * catalogue's own first/last measured depth — not from the absence of a
   * point in the downsampled stream.
   */
  uncovered: { fromMd: number; toMd: number }[]
  min: number
  max: number
  samples: number
  /** First and last depth with a real reading, or null when unknown. */
  firstMd: number | null
  lastMd: number | null
}

function buildLanes(
  points: TelemetryStreamPoint[],
  series: StreamSeries[],
  coverage: Map<string, Coverage>,
  topMd: number,
  bottomMd: number,
): Lane[] {
  return series.map((entry) => {
    const runs: { md: number; value: number }[][] = []
    let run: { md: number; value: number }[] = []
    let min = Number.POSITIVE_INFINITY
    let max = Number.NEGATIVE_INFINITY

    for (const point of points) {
      const value = point.channels[entry.channel]
      /*
       * A key absent from a downsampled point does NOT mean "unmeasured":
       * the server keeps only each bucket's min and max, so a channel is
       * absent whenever it was not that bucket's extreme. The polyline is
       * therefore drawn straight through the envelope — the kept extremes
       * are real measurements, and joining them is a choice about drawing,
       * not an invented reading. Real unmeasured depth is taken from the
       * channel catalogue below and drawn as a gap.
       */
      if (value === undefined || value === null) continue
      run.push({ md: point.md, value })
      if (value < min) min = value
      if (value > max) max = value
    }
    if (run.length > 0) runs.push(run)

    const cover = coverage.get(entry.channel)
    const uncovered: { fromMd: number; toMd: number }[] = []
    if (cover) {
      if (cover.firstMd > topMd + 1e-6) {
        uncovered.push({ fromMd: topMd, toMd: Math.min(cover.firstMd, bottomMd) })
      }
      if (cover.lastMd < bottomMd - 1e-6) {
        uncovered.push({ fromMd: Math.max(cover.lastMd, topMd), toMd: bottomMd })
      }
    }

    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      min = 0
      max = 1
    } else if (min === max) {
      min -= 0.5
      max += 0.5
    }
    return {
      series: entry,
      runs,
      uncovered,
      min,
      max,
      samples: cover?.samples ?? 0,
      firstMd: cover?.firstMd ?? null,
      lastMd: cover?.lastMd ?? null,
    }
  })
}

export function StreamPlot({
  points,
  series,
  coverage,
  topMd,
  bottomMd,
  currentBitMd,
  className,
}: {
  points: TelemetryStreamPoint[]
  series: StreamSeries[]
  /**
   * Per-channel measured depth coverage from the channel catalogue. This is
   * what makes a gap real: a channel absent from a downsampled point usually
   * just was not that bucket's extreme, so the envelope's own absences cannot
   * be read as missing data.
   */
  coverage: Map<string, Coverage>
  topMd: number
  bottomMd: number
  /** Current bit depth; drawn as a single amber rule across every lane. */
  currentBitMd: number | null
  className?: string
}) {
  const { ref, width } = useElementWidth<HTMLDivElement>(760)
  const [cursorMd, setCursorMd] = useState<number | null>(null)

  const lanes = useMemo(
    () => buildLanes(points, series, coverage, topMd, bottomMd),
    [points, series, coverage, topMd, bottomMd],
  )
  const ticks = useMemo(
    () => ticksBetween(topMd, bottomMd, niceStep((bottomMd - topMd) / 7)),
    [topMd, bottomMd],
  )
  const plotH = lanes.length * LANE_H + Math.max(lanes.length - 1, 0) * LANE_GAP
  const plotW = Math.max(width - AXIS_W - PAD_RIGHT, 200)
  const svgW = VALUE_AXIS_W + plotW + PAD_RIGHT
  const span = Math.max(bottomMd - topMd, 1e-6)

  const yOf = (md: number) => ((md - topMd) / span) * plotH
  const xOf = (lane: Lane, value: number) =>
    VALUE_AXIS_W + ((value - lane.min) / (lane.max - lane.min)) * plotW

  function onMove(event: MouseEvent<HTMLDivElement>) {
    const box = event.currentTarget.getBoundingClientRect()
    const ratio = (event.clientY - box.top) / Math.max(box.height, 1)
    const md = topMd + ratio * span
    setCursorMd(md < topMd || md > bottomMd ? null : md)
  }

  const bitVisible = currentBitMd !== null && currentBitMd >= topMd && currentBitMd <= bottomMd

  return (
    <div ref={ref} className={cx('min-w-0', className)}>
      <div className="flex gap-0">
        <DepthAxis topM={topMd} bottomM={bottomMd} labels={ticks} className="h-auto" />
        <div className="min-w-0 flex-1">
          <ul
            className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5 pb-1"
            style={{ minHeight: HEADER_H }}
          >
            {lanes.map((lane) => (
              <li key={lane.series.channel} className="flex items-baseline gap-1.5">
                <span
                  aria-hidden
                  className="inline-block h-0.5 w-3 shrink-0 rounded-full"
                  style={{ background: lane.series.color }}
                />
                <span className="text-2xs text-fg-muted">{lane.series.channel}</span>
                <span className="tnum text-2xs text-fg-subtle">
                  {fmtNumber(lane.samples, 0)} readings
                </span>
                {lane.firstMd !== null ? (
                  <span className="tnum text-2xs text-fg-subtle">
                    measured {fmtMeters(lane.firstMd, 0)}–{fmtMeters(lane.lastMd, 0)}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>

          <div
            className="plot-surface overflow-hidden"
            onMouseMove={onMove}
            onMouseLeave={() => setCursorMd(null)}
          >
            <svg
              width={svgW}
              height={plotH}
              role="img"
              aria-label={`Measured depth plot of ${series.map((s) => s.channel).join(', ')} joining ${points.length} retained min/max envelope points from ${fmtMeters(topMd, 1)} to ${fmtMeters(bottomMd, 1)} metres MD. Depth at which a channel was never measured is left blank.`}
            >
              {ticks.map((depth) => (
                <line
                  key={depth}
                  x1={0}
                  x2={svgW}
                  y1={yOf(depth)}
                  y2={yOf(depth)}
                  stroke="var(--grid)"
                  strokeWidth={1}
                />
              ))}

              {lanes.map((lane, laneIndex) => {
                const laneTop = laneIndex * (LANE_H + LANE_GAP)
                const laneHeight = LANE_H
                return (
                  <g key={lane.series.channel} transform={`translate(0 ${laneTop})`}>
                    {lane.uncovered.map((gap) => {
                      const top = yOf(gap.fromMd)
                      const height = Math.max(yOf(gap.toMd) - top, 1)
                      const metres = gap.toMd - gap.fromMd
                      return (
                        <g key={`${gap.fromMd}-${gap.toMd}`}>
                          <rect
                            x={VALUE_AXIS_W}
                            y={top}
                            width={plotW}
                            height={height}
                            fill="var(--surface-2)"
                            stroke="var(--line)"
                            strokeDasharray="2 3"
                            strokeWidth={1}
                          />
                          {metres >= MIN_GAP_LABEL_MD && height > 10 ? (
                            <text
                              x={VALUE_AXIS_W + plotW / 2}
                              y={top + height / 2 + 3}
                              textAnchor="middle"
                              fontSize={9}
                              fill="var(--fg-subtle)"
                            >
                              no measurement logged in this interval ({fmtMeters(metres, 0)})
                            </text>
                          ) : null}
                        </g>
                      )
                    })}

                    {lane.runs.map((run, runIndex) => (
                      <polyline
                        key={runIndex}
                        fill="none"
                        stroke={lane.series.color}
                        strokeWidth={1.4}
                        strokeLinejoin="round"
                        strokeLinecap="round"
                        points={run
                          .map((sample) => `${xOf(lane, sample.value)},${yOf(sample.md)}`)
                          .join(' ')}
                      />
                    ))}

                    <text x={4} y={11} fontSize={9} fill="var(--fg-muted)" className="tnum">
                      {lane.series.unit}
                    </text>
                    <text
                      x={VALUE_AXIS_W + plotW + 4}
                      y={10}
                      fontSize={9}
                      fill="var(--fg-subtle)"
                      className="tnum"
                    >
                      {fmtNumber(lane.max, 1)}
                    </text>
                    <text
                      x={VALUE_AXIS_W + plotW + 4}
                      y={laneHeight - 3}
                      fontSize={9}
                      fill="var(--fg-subtle)"
                      className="tnum"
                    >
                      {fmtNumber(lane.min, 1)}
                    </text>
                  </g>
                )
              })}

              {bitVisible ? (
                <g>
                  <line
                    x1={0}
                    x2={svgW}
                    y1={yOf(currentBitMd)}
                    y2={yOf(currentBitMd)}
                    stroke="var(--warn)"
                    strokeWidth={1.5}
                    strokeDasharray="5 3"
                  />
                  <text
                    x={VALUE_AXIS_W + plotW}
                    y={yOf(currentBitMd) - 4}
                    textAnchor="end"
                    fontSize={9}
                    fontWeight={600}
                    fill="var(--warn)"
                  >
                    current bit {fmtMeters(currentBitMd, 1)} MD
                  </text>
                </g>
              ) : null}

              {cursorMd !== null ? (
                <line
                  x1={0}
                  x2={VALUE_AXIS_W + plotW}
                  y1={yOf(cursorMd)}
                  y2={yOf(cursorMd)}
                  stroke="var(--fg-subtle)"
                  strokeWidth={1}
                />
              ) : null}
            </svg>
          </div>
        </div>
      </div>

      <p className="mt-1 text-2xs leading-relaxed text-fg-muted">
        Depth runs down the vertical axis in metres MD; each lane has its own value scale
        because the channels carry different units. The line joins the min/max envelope
        points the server kept, so it is a drawing of real extremes — not a resampled
        series. A dashed band is depth at which the channel was genuinely never measured,
        taken from the channel catalogue&rsquo;s first and last reading; the line stops
        there and is not carried across.
        {cursorMd === null
          ? ' Hover the plot to trace a depth across every lane.'
          : ` Cursor at ${fmtMeters(cursorMd, 1)} m MD.`}
      </p>
    </div>
  )
}
