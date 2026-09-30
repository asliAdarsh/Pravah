import { useState } from 'react'
import {
  Badge,
  Callout,
  DataTable,
  Disclosure,
  KeyStat,
  LineIcon,
  ScreenHeader,
  SectionCard,
  StatRow,
  Skeleton,
  cx,
  actionButtonClass,
  ghostButtonClass,
} from '@/components/ui'
import { IntervalBar } from '@/components/visuals/IntervalBar'
import { useAsyncData } from '@/lib/useAsyncData'
import { useApp } from '@/store/useApp'
import { fmtMeters, fmtNumber, fmtPercent, humanizeEnum } from '@/lib/format'
import { ResourceState, severityFillVar } from './_shared'
import {
  TelemetryApi,
  type BacktestLeakageAudit,
  type BacktestRun,
  type TelemetryAlert,
} from './_engines'

/**
 * Screen 9 — Backtest / validation
 *
 * The hero is the one number a drilling engineer asks for: how much warning did
 * the detector give, in metres of hole and in minutes of drilling, against a
 * named real incident. Everything the backtest computes about that incident
 * hangs off it.
 *
 * The language is deliberately exact. The precision figure is a Wilson 95%
 * score interval for a stated episode definition, presented as 0.235–0.385 with
 * its n; it is not "accuracy", it is not rounded up, and the gap policy is
 * stated rather than implied. The leakage audit is the evidence that the
 * backtest did not peek, and it stays collapsed until someone asks for it.
 */

interface Milestone {
  key: string
  label: string
  depthMd: number | null
  detector: string | null
  channel: string | null
  note: string
}

/**
 * The backtest reports one depth per milestone and a single channel for the
 * first precursor. The detector and the channel actually responsible for each
 * milestone are resolved from the alert list at that depth, so the table names
 * the algorithm that fired rather than saying "mixed" three times.
 */
function milestonesOf(
  run: BacktestRun,
  alerts: TelemetryAlert[],
): Milestone[] {
  const resolve = (
    depthMd: number | null,
    band: 'any' | 'CRITICAL',
  ): { detector: string | null; channel: string | null } => {
    if (depthMd === null) return { detector: null, channel: null }
    const atDepth = alerts.filter(
      (alert) =>
        Math.abs(alert.md - depthMd) < 0.01 && (band === 'any' || alert.severity === band),
    )
    if (atDepth.length === 0) return { detector: null, channel: null }
    /* Both detectors usually fire together; name the one that fired first. */
    const first = atDepth[0]
    return { detector: first.detector, channel: first.channel }
  }

  const precursor = resolve(run.first_precursor_md, 'any')
  const critical = resolve(run.first_critical_md, 'CRITICAL')
  const sustained = resolve(run.first_sustained_md, 'any')

  return [
    {
      key: 'precursor',
      label: 'First precursor',
      depthMd: run.first_precursor_md,
      detector: precursor.detector,
      channel: precursor.channel ?? run.first_precursor_channel,
      note: 'Shallowest alert of any band on the incident hazard.',
    },
    {
      key: 'critical',
      label: 'First critical',
      depthMd: run.first_critical_md,
      detector: critical.detector,
      channel: critical.channel ?? run.first_precursor_channel,
      note: 'Shallowest alert in the CRITICAL band.',
    },
    {
      key: 'sustained',
      label: 'First sustained',
      depthMd: run.first_sustained_md,
      detector: sustained.detector,
      channel: sustained.channel ?? run.first_precursor_channel,
      note: `First run of ${run.sustained_run_length} consecutive alerting samples. Longest critical run observed: ${run.max_critical_run}.`,
    },
  ]
}

const AUDIT_ROWS: { key: keyof BacktestLeakageAudit; label: string; detail: string }[] = [
  {
    key: 'zero_future_leakage',
    label: 'No future leakage',
    detail: 'No sample after an alert point influences that alert.',
  },
  {
    key: 'chronological_order_preserved',
    label: 'Chronological order preserved',
    detail: 'Samples are consumed in log order, never shuffled.',
  },
  {
    key: 'rolling_statistics_causal',
    label: 'Causal rolling statistics',
    detail: 'The baseline is the trailing window; a sample is never in its own baseline.',
  },
  {
    key: 'cusum_state_recursive',
    label: 'Recursive CUSUM state',
    detail: 'CUSUM sums advance one sample at a time and reset on an alarm or a gap.',
  },
  {
    key: 'target_well_excluded_from_own_analogs',
    label: 'Well excluded from its own analogs',
    detail: 'Only the target well’s own telemetry feeds its detector.',
  },
  {
    key: 'historical_events_bounded_by_current_depth',
    label: 'Events bounded by current depth',
    detail: 'Events deeper than the current depth cannot enter the run.',
  },
]

export function Validation() {
  const { meta, currentWell, currentWellId, notify } = useApp()
  const [nonce, setNonce] = useState(0)
  const [running, setRunning] = useState(false)

  const run = useAsyncData(
    (signal) => TelemetryApi.backtest(currentWellId, {}, signal),
    [currentWellId, nonce],
  )
  /*
   * The milestone table resolves each depth's detector and channel from the
   * alert list, windowed to the lead interval. That endpoint is a heavy scan
   * of the whole telemetry log, so it is deliberately NOT started until the
   * backtest itself has resolved — running both at once made the backtest miss
   * the request timeout. A failure here only leaves the milestone cells
   * unresolved; it never blocks the screen.
   */
  const milestoneAlerts = useAsyncData(
    (signal) =>
      run.data
        ? TelemetryApi.alerts(
            currentWellId,
            {
              hazard: run.data.hazard,
              from_md: run.data.first_precursor_md ?? undefined,
              to_md: run.data.incident.depth_md,
              limit: 200,
            },
            signal,
          )
        : Promise.resolve(null),
    [currentWellId, run.data?.id],
  )

  async function runBacktest() {
    setRunning(true)
    try {
      const fresh = await TelemetryApi.runBacktest(currentWellId, {})
      notify(
        `Backtest ${fresh.id} — ${fmtMeters(fresh.lead_distance_m, 1)} of lead on ${humanizeEnum(fresh.hazard)}`,
        'success',
      )
      setNonce((value) => value + 1)
    } catch (cause) {
      notify(
        cause instanceof Error ? cause.message : 'Backtest run failed',
        'critical',
      )
    } finally {
      setRunning(false)
    }
  }

  const data = run.data
  const incident = data?.incident
  const precision = data?.precision
  const rop = data?.observed_rop
  const audit = data?.leakage_audit

  return (
    <div className="flex flex-col gap-4">
      <ScreenHeader
        title="Backtest &amp; validation"
        subtitle="How much warning the telemetry detectors gave on a named real incident, measured on this well's own recorded log. Re-runnable, and auditable for future-sample leakage."
        meta={
          <>
            <Badge tone="warning">{meta?.dataset_label ?? 'Real public data'}</Badge>
            <Badge tone="neutral">{currentWellId}</Badge>
            {data ? <Badge tone="info">{data.id}</Badge> : null}
          </>
        }
        actions={
          <button
            type="button"
            onClick={() => void runBacktest()}
            disabled={running}
            className={actionButtonClass}
          >
            <LineIcon name="refresh" size={12} />
            {running ? 'Running…' : 'Run backtest'}
          </button>
        }
      />

      <ResourceState
        loading={run.loading}
        error={run.error}
        onRetry={run.reload}
        skeleton={
          // The walk over tens of thousands of samples takes several seconds. A
          // bare grey block read as an empty screen, so say what is happening.
          <div className="inset-surface flex flex-col items-start gap-2 px-4 py-8">
            <Skeleton className="h-4 w-72" />
            <Skeleton className="h-3 w-96" />
            <Skeleton className="h-3 w-80" />
            <p className="mt-2 text-2xs text-fg-muted">
              Walking the recorded telemetry against the named incident — every rolling
              window and CUSUM state is recomputed from the start, so this takes a few
              seconds on a well with tens of thousands of samples.
            </p>
          </div>
        }
      >
        {data && incident && precision ? (
          <>
            {/* ----------------------------- hero ------------------------------ */}
            <SectionCard
              title={`Lead against ${incident.event_id}`}
              description={incident.source}
            >
              <StatRow>
                <KeyStat
                  label="Lead distance"
                  value={fmtMeters(data.lead_distance_m, 1)}
                  unit="of hole"
                  tone={data.lead_distance_m !== null ? 'success' : 'muted'}
                  hint={
                    data.first_precursor_md !== null
                      ? `from ${fmtMeters(data.first_precursor_md, 1)} MD to the incident at ${fmtMeters(incident.depth_md, 1)} MD`
                      : 'no precursor alert before the incident'
                  }
                />
                <KeyStat
                  label="Lead time"
                  value={
                    data.lead_minutes !== null ? fmtNumber(data.lead_minutes, 1) : '—'
                  }
                  unit="minutes"
                  tone={data.lead_minutes !== null ? 'success' : 'muted'}
                  hint={
                    rop?.available
                      ? `at the observed mean ROP of ${fmtNumber(rop.mean_m_per_h, 1)} m/h over ${fmtNumber(rop.samples, 0)} samples`
                      : 'no ROP samples across the lead interval'
                  }
                />
                <KeyStat
                  label="Incident depth"
                  value={fmtMeters(incident.depth_md, 1)}
                  unit="MD"
                  hint={`${humanizeEnum(incident.hazard)} · row ${fmtNumber(incident.row_index, 0)}`}
                />
                <KeyStat
                  label="Alerts before incident"
                  value={fmtNumber(data.alerts_before_incident, 0)}
                  hint={`of ${fmtNumber(data.alerts_total, 0)} total on this well`}
                />
              </StatRow>

              <p className="mt-3 text-xs leading-relaxed text-fg-muted">{data.lead_note}</p>

              {rop?.available ? (
                <div className="mt-2">
                  <Callout tone="info" title="Two lead-time conversions, neither presented as the single truth">
                    Mean ROP {fmtNumber(rop.mean_m_per_h, 1)} m/h over{' '}
                    {fmtNumber(rop.samples, 0)} samples between {fmtMeters(rop.from_md, 1)} and{' '}
                    {fmtMeters(rop.to_md, 1)} MD gives{' '}
                    {fmtNumber(data.lead_minutes, 1)} minutes. The median of{' '}
                    {fmtNumber(rop.median_m_per_h, 1)} m/h would give{' '}
                    {fmtNumber(
                      data.lead_distance_m !== null
                        ? (data.lead_distance_m / rop.median_m_per_h) * 60
                        : null,
                      1,
                    )}{' '}
                    minutes. The two differ because the top of the hole is drilled fast.
                  </Callout>
                </div>
              ) : null}
            </SectionCard>

            {/* -------------------------- secondary --------------------------- */}
            <div className="mt-4 grid gap-4 lg:grid-cols-7">
              <SectionCard
                className="lg:col-span-4"
                title="Detection milestones"
                description="The three depths that define the run, against the incident."
                dense
              >
                <DataTable
                  rowKey={(row) => row.key}
                  columns={[
                    { key: 'label', header: 'Milestone', width: 140 },
                    {
                      key: 'depthMd',
                      header: 'Depth',
                      align: 'right',
                      width: 110,
                      render: (row) => (
                        <span className="tnum">
                          {row.depthMd === null ? '—' : fmtMeters(row.depthMd, 1)}
                        </span>
                      ),
                    },
                    {
                      key: 'detector',
                      header: 'Detector',
                      width: 100,
                      render: (row) =>
                        row.detector ? (
                          humanizeEnum(row.detector)
                        ) : (
                          <span className="text-fg-subtle">not resolved</span>
                        ),
                    },
                    { key: 'channel', header: 'Channel' },
                  ]}
                  rows={milestonesOf(data, milestoneAlerts.data?.alerts ?? [])}
                />
                <p className="mt-2 text-2xs leading-relaxed text-fg-muted">
                  {data.precision_definition} Both detectors (z-score and CUSUM) run on every
                  watched channel, so the detector named is the first to fire at that depth, not
                  the only one.
                </p>
              </SectionCard>

              <SectionCard
                className="lg:col-span-3"
                title="Episode precision"
                description="Wilson 95% score interval on the stated episode definition."
              >
                <IntervalBar
                  label="Precision, Wilson 95% interval"
                  lower={precision.lower}
                  centre={precision.centre}
                  upper={precision.upper}
                  caption={
                    <>
                      Point estimate {fmtPercent(precision.centre, 1)} from{' '}
                      {fmtNumber(precision.successes, 0)} of {fmtNumber(precision.trials, 0)}{' '}
                      episodes, interval {fmtPercent(precision.lower, 1)}–{fmtPercent(precision.upper, 1)}{' '}
                      at 95% confidence. This is an interval on one episode definition, not a
                      measure of accuracy, and it is not comparable to a per-sample hit rate.
                    </>
                  }
                />
                <div className="mt-3 border-t border-line pt-2">
                  <p className="text-2xs font-semibold uppercase tracking-[0.06em] text-fg-muted">
                    Episodes
                  </p>
                  <dl className="mt-1 space-y-1 text-2xs text-fg-muted">
                    <div className="flex justify-between gap-2">
                      <dt>Started before the incident (true positives)</dt>
                      <dd className="tnum text-fg-strong">
                        {fmtNumber(data.episodes_before_incident, 0)}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Total episodes</dt>
                      <dd className="tnum text-fg-strong">{fmtNumber(data.episodes_total, 0)}</dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Rolling window</dt>
                      <dd className="tnum text-fg-strong">{data.window} samples</dd>
                    </div>
                  </dl>
                </div>
              </SectionCard>
            </div>

            {/* --------------------------- collapsed --------------------------- */}
            <div className="mt-4 flex flex-col gap-2">
              <Disclosure
                summary={`Leakage audit — ${audit ? 'all checks passed' : '…'}`}
                badge={<Badge tone="success">no future leakage</Badge>}
              >
                {audit ? (
                  <>
                    <ul className="space-y-1.5">
                      {AUDIT_ROWS.map((check) => {
                        const passed = audit[check.key] === true
                        return (
                          <li key={check.key} className="flex items-start gap-2">
                            <span
                              className={cx(
                                'mt-0.5 shrink-0',
                                passed ? 'text-ok' : 'text-crit',
                              )}
                            >
                              <LineIcon
                                name={passed ? 'check' : 'x'}
                                size={13}
                              />
                            </span>
                            <span className="min-w-0">
                              <span className="text-xs font-medium text-fg-strong">
                                {check.label}
                              </span>
                              <span className="block text-2xs leading-relaxed text-fg-muted">
                                {check.detail}
                              </span>
                            </span>
                          </li>
                        )
                      })}
                    </ul>

                    <div className="mt-2 border-t border-line pt-2">
                      <p className="text-2xs font-semibold uppercase tracking-[0.06em] text-fg-muted">
                        Truncation check
                      </p>
                      <p className="mt-0.5 text-2xs leading-relaxed text-fg-muted">
                        Truncating the series at the incident row and re-running the detector
                        reproduces the full run&rsquo;s alerts at or before that row, field for
                        field. A future sample influencing an earlier decision would break this
                        equality.
                      </p>
                      <dl className="mt-1.5 grid gap-x-4 gap-y-1 text-2xs sm:grid-cols-2">
                        {Object.entries(audit.truncation_check).map(([key, value]) => (
                          <div key={key} className="flex justify-between gap-2">
                            <dt className="text-fg-muted">{humanizeEnum(key)}</dt>
                            <dd className="tnum text-fg-strong">
                              {typeof value === 'boolean'
                                ? value
                                  ? 'pass'
                                  : 'FAIL'
                                : value === null
                                  ? '—'
                                  : String(value)}
                            </dd>
                          </div>
                        ))}
                      </dl>
                      <p className="mt-1.5 text-2xs text-fg-subtle">
                        Test: <span className="tnum">{audit.tests_passed}</span>
                      </p>
                    </div>

                    <ul className="mt-2 space-y-1 border-t border-line pt-2">
                      {audit.notes.map((note) => (
                        <li
                          key={note}
                          className="text-2xs leading-relaxed text-fg-muted before:mr-1.5 before:text-fg-subtle before:content-['—']"
                        >
                          {note}
                        </li>
                      ))}
                    </ul>
                  </>
                ) : null}
              </Disclosure>

              <Disclosure
                summary={`Alert statistics — ${fmtNumber(data.alerts_total, 0)} alerts`}
                badge={<Badge tone="neutral">{data.window}-sample window</Badge>}
              >
                <div className="grid gap-4 sm:grid-cols-3">
                  <div>
                    <p className="text-2xs font-semibold uppercase tracking-[0.06em] text-fg-muted">
                      By detector
                    </p>
                    <ul className="mt-1 space-y-0.5">
                      {Object.entries(data.alerts_by_detector).map(([detector, count]) => (
                        <li key={detector} className="flex justify-between gap-2 text-2xs">
                          <span className="text-fg-muted">{humanizeEnum(detector)}</span>
                          <span className="tnum text-fg-strong">{fmtNumber(count, 0)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <p className="text-2xs font-semibold uppercase tracking-[0.06em] text-fg-muted">
                      By severity band
                    </p>
                    <ul className="mt-1 space-y-0.5">
                      {Object.entries(data.alerts_by_severity).map(([band, count]) => (
                        <li key={band} className="flex justify-between gap-2 text-2xs">
                          <span
                            className="inline-flex items-center gap-1.5"
                            style={{ color: severityFillVar(band as 'CRITICAL') }}
                          >
                            <span
                              aria-hidden
                              className="size-1.5 rounded-full"
                              style={{ background: severityFillVar(band as 'CRITICAL') }}
                            />
                            {humanizeEnum(band)}
                          </span>
                          <span className="tnum text-fg-strong">{fmtNumber(count, 0)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <p className="text-2xs font-semibold uppercase tracking-[0.06em] text-fg-muted">
                      Channels evaluated
                    </p>
                    <ul className="mt-1 space-y-0.5">
                      {data.channels_evaluated.map((channel) => (
                        <li key={channel.channel} className="flex justify-between gap-2 text-2xs">
                          <span className="truncate text-fg-muted" title={channel.channel}>
                            {channel.channel}
                          </span>
                          <span className="tnum shrink-0 text-fg-strong">
                            {fmtNumber(channel.alerts, 0)} /{' '}
                            {fmtNumber(channel.samples, 0)}
                          </span>
                        </li>
                      ))}
                    </ul>
                    <p className="mt-1 text-2xs text-fg-subtle">alerts / samples</p>
                  </div>
                </div>
                <p className="mt-2 border-t border-line pt-2 text-2xs leading-relaxed text-fg-muted">
                  {data.gap_policy}
                </p>
              </Disclosure>

              <Disclosure summary="How to read this backtest">
                <p className="text-2xs leading-relaxed text-fg-muted">
                  The run replays this well&rsquo;s recorded telemetry through the same z-score and
                  CUSUM detectors the alert centre uses, then asks one question: did the first
                  precursor alert land above the incident? Lead distance is the hole between the two
                  depths; lead time converts that hole to minutes at the ROP actually observed over
                  the interval, and is reported at both mean and median ROP because the top of the
                  hole is drilled fast and a single conversion would overstate the warning.
                </p>
                <p className="mt-1.5 text-2xs leading-relaxed text-fg-muted">
                  Lead distance is a property of this well and this incident, not a fleet statistic.
                  The precision interval is over episodes, and an episode is a group of alerts on
                  one channel separated by fewer than the rolling window in measured samples — a
                  detector that fires 50 times in a row has produced one episode, not 50. Samples
                  with no reading are skipped rather than filled, and a gap resets the CUSUM sums
                  instead of carrying a stale accumulator across an interval nobody measured.
                </p>
                <p className="mt-1.5 text-2xs leading-relaxed text-fg-subtle">
                  {currentWell
                    ? `${currentWell.name} is at ${fmtMeters(currentWell.current_depth_md)} MD in ${currentWell.current_formation.name}. `
                    : ''}
                  Detector thresholds are engine defaults, not operational guidance.
                </p>
              </Disclosure>
            </div>
          </>
        ) : null}
      </ResourceState>

      {run.data ? (
        <div className="flex justify-end">
          <button type="button" onClick={run.reload} className={ghostButtonClass}>
            <LineIcon name="refresh" size={12} />
            Refresh result
          </button>
        </div>
      ) : null}
    </div>
  )
}
