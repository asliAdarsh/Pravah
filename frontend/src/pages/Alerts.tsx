import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Api } from '@/api/client'
import type { AlertBrief, AlertStatus, SeverityBand } from '@/api/types'
import {
  Badge,
  Disclosure,
  EmptyState,
  KeyStat,
  LineIcon,
  ScreenHeader,
  SectionCard,
  StatRow,
  Toolbar,
  actionButtonClass,
  controlClass,
  cx,
  ghostButtonClass,
} from '@/components/ui'
import { useAsyncData } from '@/lib/useAsyncData'
import { useApp } from '@/store/useApp'
import {
  bandDotClass,
  bandTone,
  fmtDateTime,
  fmtMeters,
  fmtNumber,
  humanizeEnum,
  scoreMethodLabel,
} from '@/lib/format'
import { LABEL, ResourceState } from './_shared'
import {
  ProxyTag,
  SliceNote,
  isTelemetryAlert,
  useBounded,
} from './_calm'

/* ------------------------------------------------------------------ *
 * Screen 5 — Alert centre
 *
 * The full alert set is fetched once and every filter is applied in the
 * client, so the severity summary always describes the whole set rather than
 * whatever subset happens to be on screen. The API's own `counts` block is
 * used verbatim for the status row.
 *
 * The list is the hero and it is grouped by severity band — band order, then
 * risk score inside each band, so the top of the page is the top of the
 * problem. The rule that produced the scores is reference material and lives
 * in a Disclosure, not loose in the page.
 * ------------------------------------------------------------------ */

const STATUSES: AlertStatus[] = ['OPEN', 'ACKNOWLEDGED', 'DISMISSED', 'CLOSED']
const BANDS: SeverityBand[] = ['CRITICAL', 'HIGH', 'WARNING', 'INFO']
const STATUS_TONE = {
  OPEN: 'critical',
  ACKNOWLEDGED: 'warning',
  DISMISSED: 'muted',
  CLOSED: 'success',
} as const

const BAND_BLURB: Record<SeverityBand, string> = {
  CRITICAL: 'Closest analogue to the current well — act before the next casing point.',
  HIGH: 'Same pattern, weaker match. Review before it reaches this interval.',
  WARNING: 'Pattern present, relevance below the reporting threshold.',
  INFO: 'Logged for context only. No action implied.',
}

export function Alerts() {
  const { meta, riskConfig, openDecision } = useApp()
  const { alertId } = useParams<{ alertId: string }>()

  const [status, setStatus] = useState<AlertStatus | ''>('')
  const [band, setBand] = useState<SeverityBand | ''>('')
  const [eventType, setEventType] = useState('')
  const [well, setWell] = useState('')

  const alerts = useAsyncData((signal) => Api.alerts({}, signal), [])

  const all = useMemo(() => alerts.data?.items ?? [], [alerts.data])

  /* `/alerts/:alertId` is mirrored into ?alert= by the route bridge; call the
     store too so the panel opens whichever way the URL was reached. */
  useEffect(() => {
    if (alertId) openDecision(alertId)
  }, [alertId, openDecision])

  const bandCounts = useMemo(() => {
    const counts: Record<string, number> = { CRITICAL: 0, HIGH: 0, WARNING: 0, INFO: 0 }
    for (const a of all) counts[a.severity_band] = (counts[a.severity_band] ?? 0) + 1
    return counts
  }, [all])

  const wells = useMemo(() => [...new Set(all.map((a) => a.current_well_id))].sort(), [all])
  const eventTypes = useMemo(() => [...new Set(all.map((a) => a.event_type))].sort(), [all])

  const filtered = useMemo(
    () =>
      all
        .filter((a) => (status ? a.status === status : true))
        .filter((a) => (band ? a.severity_band === band : true))
        .filter((a) => (eventType ? a.event_type === eventType : true))
        .filter((a) => (well ? a.current_well_id === well : true)),
    [all, status, band, eventType, well],
  )

  /* Hero ordering: band severity first, risk score inside the band. */
  const grouped = useMemo(
    () =>
      BANDS.map((b) => ({
        band: b,
        items: filtered
          .filter((a) => a.severity_band === b)
          .sort((x, y) => y.risk_score - x.risk_score),
      })).filter((group) => group.items.length > 0),
    [filtered],
  )

  /* No band may render an unbounded grid. Each band slices independently so a
     long tail of INFO alerts cannot bury a single CRITICAL one. */
  /* One slice per band, all four hooks unconditional: the band list is fixed,
     so a long tail of INFO alerts cannot bury a single CRITICAL one. */
  const rowsFor = (band: SeverityBand) => filtered.filter((a) => a.severity_band === band)
  const criticalSlice = useBounded(rowsFor('CRITICAL'), 6, 12)
  const highSlice = useBounded(rowsFor('HIGH'), 6, 12)
  const warningSlice = useBounded(rowsFor('WARNING'), 6, 12)
  const infoSlice = useBounded(rowsFor('INFO'), 6, 12)
  const bandSlices: Record<SeverityBand, ReturnType<typeof useBounded<AlertBrief>>> = {
    CRITICAL: criticalSlice,
    HIGH: highSlice,
    WARNING: warningSlice,
    INFO: infoSlice,
  }

  const openCount = all.filter((a) => a.status === 'OPEN').length
  const activeAlerts = all.filter((a) => a.is_active).length
  const filtersOn = Boolean(status || band || eventType || well)

  const reset = () => {
    setStatus('')
    setBand('')
    setEventType('')
    setWell('')
  }

  return (
    <div className="flex flex-col gap-4">
      <ScreenHeader
        title="Alert centre"
        subtitle="Hazards the anomaly engine raised on live MWD and mud-logger telemetry, grouped by severity band. Opening an alert renders the full decision panel."
        meta={
          <>
            <Badge tone="warning">{meta?.dataset_label ?? 'Real public data'}</Badge>
            <Badge tone={openCount > 0 ? 'critical' : 'muted'}>
              {fmtNumber(openCount, 0)} open of {fmtNumber(all.length, 0)}
            </Badge>
          </>
        }
      />

      <SectionCard title="Alerts by severity band" dense>
        <Toolbar className="-mx-2 -mt-2 mb-3 flex flex-wrap items-end gap-x-4 gap-y-3 rounded-t-md border-b">
          <div className="w-44">
            <label className={LABEL} htmlFor="alert-status">
              Status
            </label>
            <select
              id="alert-status"
              className={cx(controlClass, 'mt-1 w-full')}
              value={status}
              onChange={(e) => setStatus(e.target.value as AlertStatus | '')}
            >
              <option value="">Any status</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {`${humanizeEnum(s)} (${alerts.data?.counts?.[s] ?? 0})`}
                </option>
              ))}
            </select>
          </div>

          <div className="w-44">
            <label className={LABEL} htmlFor="alert-band">
              Severity band
            </label>
            <select
              id="alert-band"
              className={cx(controlClass, 'mt-1 w-full')}
              value={band}
              onChange={(e) => setBand(e.target.value as SeverityBand | '')}
            >
              <option value="">Any band</option>
              {BANDS.map((b) => (
                <option key={b} value={b}>
                  {`${b} (${bandCounts[b] ?? 0})`}
                </option>
              ))}
            </select>
          </div>

          <div className="w-48">
            <label className={LABEL} htmlFor="alert-type">
              Event type
            </label>
            <select
              id="alert-type"
              className={cx(controlClass, 'mt-1 w-full')}
              value={eventType}
              onChange={(e) => setEventType(e.target.value)}
            >
              <option value="">All event types</option>
              {eventTypes.map((t) => (
                <option key={t} value={t}>
                  {humanizeEnum(t)}
                </option>
              ))}
            </select>
          </div>

          <div className="w-44">
            <label className={LABEL} htmlFor="alert-well">
              Well
            </label>
            <select
              id="alert-well"
              className={cx(controlClass, 'mt-1 w-full')}
              value={well}
              onChange={(e) => setWell(e.target.value)}
            >
              <option value="">All wells</option>
              {wells.map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
            </select>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <span className="text-2xs text-fg-muted">
              {filtersOn ? `${filtered.length} of ${all.length} shown` : `${all.length} shown`}
            </span>
            {filtersOn ? (
              <button type="button" className={ghostButtonClass} onClick={reset}>
                <LineIcon name="close" size={12} />
                Clear filters
              </button>
            ) : null}
          </div>
        </Toolbar>

        {/* One telemetry-sourced line instead of three stat boxes plus four band
            tiles: the band select above already carries the per-band counts and
            the header meta carries open-of-total. */}
        <p className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs text-fg-subtle">
          <ProxyTag>Live telemetry</ProxyTag>
          <span className="tnum">
            {`${fmtNumber(activeAlerts, 0)} active · ${fmtNumber(openCount, 0)} open · ${fmtNumber(all.length, 0)} total`}
          </span>
          <span>
            Raised by the anomaly engine on the well&apos;s own sensor stream. Zero supporting
            offset wells is correct here: the Directorate publishes no per-wellbore DDR
            attribution.
          </span>
        </p>

        <ResourceState
          loading={alerts.loading && !alerts.data}
          error={alerts.error}
          onRetry={alerts.reload}
          skeleton={
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <div className="skeleton-block h-40 rounded-md" />
              <div className="skeleton-block h-40 rounded-md" />
            </div>
          }
        >
          {filtered.length === 0 ? (
            <EmptyState
              icon={<LineIcon name="alert" size={22} />}
              title={
                all.length === 0
                  ? 'No alerts in the record'
                  : 'No alert matches these filters'
              }
              hint={
                all.length === 0
                  ? 'The rule engine did not raise any hazard for the seeded well set.'
                  : 'Clear a filter, or switch the status back to “Any status” to include acknowledged and dismissed alerts.'
              }
            />
          ) : (
            <div className="flex flex-col gap-4">
              {grouped.map((group) => (
                <section key={group.band} aria-label={`${group.band} alerts`}>
                  <header className="sticky top-0 z-10 mb-2 flex items-center gap-2 border-b border-line bg-surface-1 py-1.5">
                    <span className={cx('h-2.5 w-2.5 rounded-sm', bandDotClass(group.band))} />
                    <h3 className="text-xs font-semibold uppercase tracking-[0.07em] text-fg-strong">
                      {group.band}
                    </h3>
                    <span className="tnum rounded-sm border border-line bg-surface-2 px-1.5 text-2xs font-semibold text-fg-muted">
                      {bandSlices[group.band].shown} of {group.items.length}
                    </span>
                    <p className="ml-2 hidden truncate text-2xs text-fg-subtle lg:block">
                      {BAND_BLURB[group.band]}
                    </p>
                  </header>
                  <>
                    <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
                      {bandSlices[group.band].visible.map((a) => (
                        <AlertCard key={a.id} alert={a} onOpen={() => openDecision(a.id)} />
                      ))}
                    </ul>
                    <SliceNote
                      className="mt-2"
                      shown={bandSlices[group.band].shown}
                      total={group.items.length}
                      noun={`${group.band.toLowerCase()} alerts`}
                      onMore={bandSlices[group.band].more}
                    />
                  </>
                </section>
              ))}
            </div>
          )}
        </ResourceState>
      </SectionCard>

      <Disclosure
        summary="Rule, thresholds and how risk is computed"
        badge={<Badge tone="muted">method</Badge>}
      >
        <div className="flex flex-col gap-2 text-2xs leading-relaxed text-fg-muted">
          <p>
            Risk score method:{' '}
            <span className="font-semibold text-fg">{scoreMethodLabel()}</span>{' '}
            — an engine heuristic, not a validated model. Alert text is produced by the rule
            engine as deterministic templates; no language model is involved in any number or
            sentence on this screen.
          </p>
          <p>
            Every alert on this screen is raised by <span className="font-semibold text-fg">RULE_TELEMETRY_ANOMALY</span>{' '}
            on the well&apos;s own MWD / mud-logger stream — not by comparing offset wells. That is
            why <span className="font-semibold text-fg">supporting_well_count</span> is 0: the
            Directorate publishes no per-wellbore DDR attribution, so there is nothing to attribute
            an alarm to. The count is a property of the source, not a missing datum.
          </p>
          {riskConfig ? (
            <p>
              The offset-history rule is still configured and still applies where per-wellbore
              attribution exists: it fires with at least {riskConfig.min_support_wells} supporting
              wells within ±{riskConfig.tvd_tolerance_m} m TVD of the interval, at relevance ≥{' '}
              {riskConfig.min_relevance}
              {riskConfig.formation_match_required ? ', in the current formation' : ''}. Bands:{' '}
              WARNING ≥ {riskConfig.severity_thresholds.WARNING}, HIGH ≥{' '}
              {riskConfig.severity_thresholds.HIGH}, CRITICAL ≥{' '}
              {riskConfig.severity_thresholds.CRITICAL}.
            </p>
          ) : null}
          <p>
            Thresholds, tolerances and weights are editable in{' '}
            <Link to="/settings" className="font-semibold text-accent hover:underline">
              Settings → Risk engine
            </Link>
            . Engine defaults — they are not scientifically validated.
          </p>
        </div>
      </Disclosure>
    </div>
  )
}

function AlertCard({ alert, onOpen }: { alert: AlertBrief; onOpen: () => void }) {
  const intervalHeight = Math.max(alert.interval.bottom_tvd - alert.interval.top_tvd, 1)
  return (
    <li className="panel-surface flex min-w-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <span
          className={cx('h-2.5 w-2.5 shrink-0 rounded-sm', bandDotClass(alert.severity_band))}
        />
        <span className="tnum text-2xs font-semibold text-fg-muted">{alert.id}</span>
        <Badge tone={STATUS_TONE[alert.status]}>{alert.status}</Badge>
        {!alert.is_active ? <Badge tone="muted">inactive</Badge> : null}
        <span className="ml-auto text-2xs text-fg-subtle">{fmtDateTime(alert.created_at)}</span>
      </div>

      <div className="flex-1 px-3 py-2">
        <p className="text-xs font-semibold text-fg-strong">{alert.title}</p>
        <p className="mt-1 text-2xs leading-relaxed text-fg-muted">{alert.headline}</p>

        <StatRow className="mt-2 !grid-cols-3">
          <KeyStat
            label="Risk"
            value={alert.risk_score.toFixed(2)}
            tone={bandTone(alert.severity_band)}
          />
          <KeyStat label="Current TVD" value={fmtMeters(alert.current_tvd)} />
          <KeyStat label="To interval" value={`${alert.distance_to_interval_m} m`} />
        </StatRow>

        <dl className="mt-2 flex flex-col gap-0.5">
          <Row label="Event type" value={alert.event_label} />
          <Row
            label="Historical interval"
            value={`${fmtMeters(alert.interval.top_tvd)} – ${fmtMeters(alert.interval.bottom_tvd)}`}
            mono
          />
          <Row label="Interval thickness" value={`${fmtNumber(intervalHeight, 0)} m`} mono />
          <Row label="Formation" value={`${alert.formation} · ${alert.formation_match}`} mono />
          <Row
            label="Evidence"
            value={
              isTelemetryAlert(alert)
                ? `Live telemetry · ${alert.supporting_event_count} alarm observations`
                : `${alert.supporting_well_count} wells / ${alert.supporting_event_count} events`
            }
            mono
          />
          <Row label="Current well" value={alert.current_well_name} mono />
        </dl>

        <div className="mt-2 flex items-center gap-1.5">
          <span className="text-2xs uppercase tracking-wider text-fg-muted">TVD</span>
          <svg
            viewBox="0 0 120 10"
            className="h-2.5 flex-1"
            role="img"
            aria-label={`Interval from ${alert.interval.top_tvd} to ${alert.interval.bottom_tvd} metres TVD`}
          >
            <line
              x1={0}
              y1={5}
              x2={120}
              y2={5}
              stroke="var(--grid)"
              strokeWidth={4}
            />
            <line
              x1={((alert.current_tvd - alert.interval.top_tvd) / intervalHeight) * 120}
              y1={0}
              x2={((alert.current_tvd - alert.interval.top_tvd) / intervalHeight) * 120}
              y2={10}
              stroke="var(--warn)"
              strokeWidth={2}
            />
            <rect
              x={0}
              y={3}
              width={120}
              height={4}
              fill="var(--band-critical)"
              opacity={0.5}
            />
          </svg>
        </div>
      </div>

      <div className="flex items-center gap-2 border-t border-line px-3 py-2">
        <button type="button" className={actionButtonClass} onClick={onOpen}>
          <LineIcon name="alert" size={12} />
          Decision panel
        </button>
        <span className="ml-auto text-2xs text-fg-subtle">
          {alert.id} · risk {alert.risk_score.toFixed(2)}
        </span>
      </div>
    </li>
  )
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="shrink-0 text-2xs uppercase tracking-wider text-fg-subtle">{label}</dt>
      <dd className={cx('min-w-0 truncate text-right text-2xs text-fg', mono && 'tnum')}>
        {value}
      </dd>
    </div>
  )
}

export default Alerts
