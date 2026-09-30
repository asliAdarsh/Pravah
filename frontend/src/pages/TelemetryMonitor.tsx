import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Badge,
  Callout,
  DataTable,
  Disclosure,
  KeyStat,
  LineIcon,
  ScreenHeader,
  SectionCard,
  SegmentedControl,
  StatRow,
  cx,
  ghostButtonClass,
} from '@/components/ui'
import { StreamPlot, type StreamSeries } from '@/components/visuals/StreamPlot'
import { useAsyncData } from '@/lib/useAsyncData'
import { useApp } from '@/store/useApp'
import { fmtMeters, fmtNumber, humanizeEnum } from '@/lib/format'
import { LABEL, ResourceState, severityFillVar } from './_shared'
import { TelemetryApi, type TelemetryChannelInfo } from './_engines'

/**
 * Screen 8 — Telemetry monitor
 *
 * The hero answers "where is the bit and what is the rig reading": current
 * depth, formation, and the watched channels with their units. The one
 * secondary panel is the depth-indexed stream, one lane per channel.
 *
 * Two honesty rules drive the screen. A channel with no reading at a row is
 * absent, never zero, and the plot breaks its line there instead of
 * interpolating across the hole. And a channel whose newest sample is not from
 * the anchor row is marked stale, because a reading from three hundred rows ago
 * is not a current value.
 *
 * The full 18-channel catalogue and the detector alert list are reference data
 * and stay closed.
 */

/** Lane colours in draw order. Theme tokens only — no raw hex. */
const LANE_COLORS = [
  'var(--band-high)',
  'var(--accent)',
  'var(--ok)',
  'var(--band-warning)',
  'var(--chrome-2)',
  'var(--band-info)',
] as const

/** Channels the hazard detectors actually watch, preselected for the plot. */
const DEFAULT_CHANNELS = [
  'Corrected Total Hookload kkgf',
  'Average Hookload kkgf',
  'Mud Density Out g/cm3',
]

const MAX_LANES = 6
const HERO_CHANNEL_PREVIEW = 9

type Density = 'sparse' | 'standard' | 'dense'

const DENSITY: Record<Density, number> = { sparse: 60, standard: 160, dense: 400 }

export function TelemetryMonitor() {
  const { meta, currentWell, currentWellId, notify } = useApp()
  const [searchParams] = useSearchParams()
  /* `/telemetry?well=…` deep-links a well; the global switcher is the default. */
  const wellId = searchParams.get('well') ?? currentWellId

  const [selected, setSelected] = useState<string[]>(DEFAULT_CHANNELS)
  const [density, setDensity] = useState<Density>('standard')
  const [showAllChannels, setShowAllChannels] = useState(false)

  const channels = useAsyncData((signal) => TelemetryApi.channels(wellId, signal), [wellId])
  const snapshot = useAsyncData((signal) => TelemetryApi.snapshot(wellId, signal), [wellId])
  const stream = useAsyncData(
    (signal) =>
      TelemetryApi.stream(wellId, { channels: selected, max_points: DENSITY[density] }, signal),
    [wellId, selected.join('|'), density],
  )
  const criticalAlerts = useAsyncData(
    (signal) => TelemetryApi.alerts(wellId, { severity: 'CRITICAL', limit: 8 }, signal),
    [wellId],
  )

  const channelList = channels.data?.channels ?? []
  /* Watched channels first — the ones a hazard rule can actually fire on. */
  const channelOrder = useMemo(
    () => [
      ...channelList.filter((entry) => entry.watched_for_hazards.length > 0),
      ...channelList.filter((entry) => entry.watched_for_hazards.length === 0),
    ],
    [channelList],
  )

  function toggleChannel(channel: string) {
    setSelected((previous) => {
      if (previous.includes(channel)) {
        /* An empty plot explains nothing; keep the last channel selected. */
        return previous.length === 1 ? previous : previous.filter((name) => name !== channel)
      }
      return previous.length >= MAX_LANES ? previous : [...previous, channel]
    })
  }

  const series: StreamSeries[] = selected.map((channel, index) => ({
    channel,
    unit: channelList.find((entry) => entry.channel === channel)?.unit ?? '',
    color: LANE_COLORS[index % LANE_COLORS.length],
  }))

  /*
   * Real measured coverage per channel, from the catalogue. A channel absent
   * from a downsampled stream point usually only means it was not that
   * bucket's min or max, so this — not the envelope's absences — is what
   * decides where a genuine gap is drawn.
   */
  const coverage = useMemo(
    () =>
      new Map(
        channelList.map((entry) => [
          entry.channel,
          { firstMd: entry.min_md, lastMd: entry.max_md, samples: entry.samples },
        ]),
      ),
    [channelList],
  )

  const snap = snapshot.data
  const streamData = stream.data
  const topMd = streamData?.depth_range_md[0] ?? 0
  const bottomMd = streamData?.depth_range_md[1] ?? 1
  const watched = snap?.channels.filter((entry) => entry.hazards.length > 0) ?? []
  const unwatched = snap?.channels.filter((entry) => entry.hazards.length === 0) ?? []
  const staleCount = snap?.channels.filter((entry) => entry.stale).length ?? 0
  const alertItems = criticalAlerts.data?.alerts ?? []

  return (
    <div className="flex flex-col gap-4">
      <ScreenHeader
        title="Telemetry monitor"
        subtitle="Real MWD and mud-logger measurements for the current well, read against its hazard detectors. Gaps are drawn as gaps — nothing is interpolated across an interval the rig did not measure."
        meta={
          <>
            <Badge tone="warning">{meta?.dataset_label ?? 'Real public data'}</Badge>
            <Badge tone="neutral">{wellId}</Badge>
            {channels.data ? (
              <Badge tone="info">
                {channels.data.channel_count} channels ·{' '}
                {fmtNumber(channels.data.total_channel_samples, 0)} readings
              </Badge>
            ) : null}
          </>
        }
      />

      <ResourceState
        loading={snapshot.loading || channels.loading}
        error={snapshot.error ?? channels.error}
        onRetry={() => {
          snapshot.reload()
          channels.reload()
        }}
        skeleton={<div className="h-48 skeleton-block" />}
      >
        {/* ------------------------------- hero ------------------------------ */}
        <SectionCard
          title={`Current snapshot — ${snap?.well_name ?? wellId}`}
          description={
            snap
              ? `Latest real reading at or before row ${fmtNumber(snap.anchor_row_index, 0)} of a ${fmtNumber(snap.at_row, 0)}-row log. ${staleCount} of ${snap.channels.length} channels carry a stale reading.`
              : undefined
          }
        >
          <StatRow>
            <KeyStat
              label="Current depth"
              value={fmtMeters(snap?.current_depth_md ?? currentWell?.current_depth_md)}
              unit="MD"
              hint={currentWell ? `TVD ${fmtMeters(currentWell.current_tvd)}` : undefined}
            />
            <KeyStat
              label="Formation"
              value={currentWell?.current_formation.name ?? '—'}
              hint={
                currentWell
                  ? `${fmtMeters(currentWell.current_formation.top_depth)}–${fmtMeters(currentWell.current_formation.bottom_depth)}`
                  : undefined
              }
            />
            <KeyStat
              label="Logged interval"
              value={
                channels.data
                  ? `${fmtMeters(channels.data.depth_range_md[0], 1)}–${fmtMeters(channels.data.depth_range_md[1], 1)}`
                  : '—'
              }
              unit="MD"
              hint={channels.data ? `${fmtNumber(channels.data.rows, 0)} rows` : undefined}
            />
            <KeyStat
              label="Watched channels"
              value={`${watched.length} of ${snap?.channels.length ?? 0}`}
              hint="at least one hazard rule reads them"
            />
          </StatRow>

          {watched.length > 0 ? (
            <>
              <ul className="mt-3 grid gap-x-4 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
                {(showAllChannels ? watched : watched.slice(0, HERO_CHANNEL_PREVIEW)).map(
                  (entry) => (
                    <li key={entry.channel} className="min-w-0">
                      <p className="truncate text-2xs font-medium uppercase tracking-[0.05em] text-fg-muted">
                        {entry.channel}
                      </p>
                      <p className="tnum mt-0.5 text-sm text-fg-strong">
                        {fmtNumber(entry.value, 2)}{' '}
                        <span className="text-2xs font-normal text-fg-muted">{entry.unit}</span>{' '}
                        {entry.stale ? (
                          <span
                            className="text-2xs font-normal text-warn"
                            title={`Newest reading is row ${entry.row_index}, not the anchor row`}
                          >
                            stale
                          </span>
                        ) : null}
                      </p>
                      <p className="tnum text-2xs text-fg-subtle">
                        measured at {fmtMeters(entry.md, 1)} MD
                      </p>
                    </li>
                  ),
                )}
              </ul>
              <div className="mt-2.5 flex flex-wrap items-center gap-2">
                {watched.length > HERO_CHANNEL_PREVIEW ? (
                  <button
                    type="button"
                    onClick={() => setShowAllChannels((value) => !value)}
                    className={ghostButtonClass}
                  >
                    {showAllChannels
                      ? `Show first ${HERO_CHANNEL_PREVIEW}`
                      : `Show all ${watched.length}`}
                  </button>
                ) : null}
                {unwatched.length > 0 ? (
                  <span className="text-2xs text-fg-subtle">
                    {unwatched.length} further channels have no hazard watcher — see the catalogue
                    below.
                  </span>
                ) : null}
              </div>
            </>
          ) : null}

          {staleCount > 0 ? (
            <div className="mt-3">
              <Callout
                tone="warning"
                title="Newest recorded readings, not live values"
              >
                {staleCount} of {snap?.channels.length} channels have their newest measurement at
                a row other than the anchor sample. This is a recorded drilling run: a channel the
                rig stopped logging stops updating, so it is shown at its last measured value and
                marked stale rather than held flat.
              </Callout>
            </div>
          ) : null}

          {snap && snap.channels_without_data.length > 0 ? (
            <div className="mt-2">
              <Callout tone="warning" title="Channels with no reading at all">
                {snap.channels_without_data.join(', ')} — never logged on this well. They are not
                shown as zero.
              </Callout>
            </div>
          ) : null}
        </SectionCard>

        {/* ---------------------------- secondary ---------------------------- */}
        <div className="mt-4">
          <SectionCard
            title="Measured stream by depth"
            description="One lane per selected channel, depth on the shared vertical axis. Each lane carries its own value scale because the units differ."
            actions={
              <SegmentedControl<Density>
                label="Plot resolution"
                size="sm"
                value={density}
                onChange={setDensity}
                options={[
                  { id: 'sparse', label: 'Coarse', hint: `${DENSITY.sparse} buckets` },
                  { id: 'standard', label: 'Standard', hint: `${DENSITY.standard} buckets` },
                  { id: 'dense', label: 'Fine', hint: `${DENSITY.dense} buckets` },
                ]}
              />
            }
          >
            <ResourceState
              loading={stream.loading}
              error={stream.error}
              onRetry={stream.reload}
              skeleton={<div className="h-72 skeleton-block" />}
            >
              {streamData ? (
                <StreamPlot
                  points={streamData.points}
                  series={series}
                  coverage={coverage}
                  topMd={topMd}
                  bottomMd={bottomMd}
                  currentBitMd={snap?.current_depth_md ?? currentWell?.current_depth_md ?? null}
                />
              ) : null}
            </ResourceState>

            {streamData ? (
              <p className="mt-2 text-2xs leading-relaxed text-fg-subtle">
                {streamData.points_returned} min/max envelope points retained from{' '}
                {fmtNumber(streamData.rows_in_window, 0)} rows across {streamData.buckets}{' '}
                buckets, so a single-sample excursion always survives the reduction. Values
                between the retained extremes exist in the log but are not drawn.
              </p>
            ) : null}

            {/* Channel selection lives with the plot it controls. */}
            <div className="mt-3 border-t border-line pt-2">
              <p className={LABEL}>
                Channels plotted — {selected.length} of {channelList.length} selected,{' '}
                {MAX_LANES} maximum
              </p>
              <ul className="mt-1.5 flex flex-wrap gap-1.5">
                {channelOrder.map((entry) => {
                  const on = selected.includes(entry.channel)
                  return (
                    <li key={entry.channel}>
                      <button
                        type="button"
                        onClick={() => toggleChannel(entry.channel)}
                        aria-pressed={on}
                        className={cx(
                          'inline-flex h-6 items-center gap-1.5 rounded-sm border px-2 text-2xs font-medium',
                          on
                            ? 'border-accent-line bg-accent-soft text-accent'
                            : 'border-line bg-surface-1 text-fg-muted hover:border-line-strong hover:text-fg-strong',
                        )}
                      >
                        {on ? <LineIcon name="check" size={11} /> : null}
                        {entry.channel}
                        {entry.watched_for_hazards.length === 0 ? (
                          <span className="text-fg-subtle">· no watcher</span>
                        ) : null}
                      </button>
                    </li>
                  )
                })}
              </ul>
            </div>
          </SectionCard>
        </div>

        {/* --------------------------- collapsed ----------------------------- */}
        <div className="mt-4 flex flex-col gap-2">
          <Disclosure
            summary={`Channel catalogue — ${channelList.length} channels`}
            badge={
              channels.data ? (
                <Badge tone="neutral">
                  {fmtNumber(channels.data.total_channel_samples, 0)} readings
                </Badge>
              ) : null
            }
          >
            {channels.data ? (
              <>
                <p className="mb-2 text-2xs leading-relaxed text-fg-muted">
                  {channels.data.note}{' '}
                  {channels.data.unwatched_channel_count} of {channels.data.channel_count}{' '}
                  channels are watched by no hazard rule.
                </p>
                <DataTable<TelemetryChannelInfo>
                  rowKey={(row) => row.channel}
                  columns={[
                    { key: 'channel', header: 'Channel' },
                    { key: 'unit', header: 'Unit', width: 72 },
                    {
                      key: 'samples',
                      header: 'Readings',
                      align: 'right',
                      width: 92,
                      render: (row) => (
                        <span className="tnum">{fmtNumber(row.samples, 0)}</span>
                      ),
                    },
                    {
                      key: 'md',
                      header: 'Depth range',
                      align: 'right',
                      width: 150,
                      render: (row) => (
                        <span className="tnum">
                          {fmtMeters(row.min_md, 1)}–{fmtMeters(row.max_md, 1)}
                        </span>
                      ),
                    },
                    {
                      key: 'watched',
                      header: 'Watched for',
                      render: (row) =>
                        row.watched_for_hazards.length === 0 ? (
                          <span className="text-fg-subtle">—</span>
                        ) : (
                          <span className="flex flex-wrap gap-1">
                            {row.watched_for_hazards.map((hazard) => (
                              <Badge key={hazard} tone="info">
                                {humanizeEnum(hazard)}
                              </Badge>
                            ))}
                          </span>
                        ),
                    },
                  ]}
                  rows={channelList}
                />
              </>
            ) : null}
          </Disclosure>

          <Disclosure
            summary={`Critical detector alerts — ${
              criticalAlerts.data ? fmtNumber(criticalAlerts.data.total, 0) : '…'
            }`}
            badge={<Badge tone="critical">critical band only</Badge>}
          >
            <ResourceState
              loading={criticalAlerts.loading}
              error={criticalAlerts.error}
              onRetry={criticalAlerts.reload}
            >
              {criticalAlerts.data ? (
                <>
                  <p className="mb-2 text-2xs leading-relaxed text-fg-muted">
                    Showing {alertItems.length} of {fmtNumber(criticalAlerts.data.total, 0)}{' '}
                    critical alerts on a rolling window of {criticalAlerts.data.window} samples.{' '}
                    {criticalAlerts.data.gap_policy}
                  </p>
                  <DataTable
                    rowKey={(row) => `${row.row_index}-${row.channel}-${row.detector}`}
                    onRowClick={(row) =>
                      notify(
                        `${row.channel} at ${fmtMeters(row.md, 1)} MD — ${row.message}`,
                        row.severity === 'CRITICAL' ? 'critical' : 'warning',
                      )
                    }
                    columns={[
                      {
                        key: 'md',
                        header: 'Depth',
                        width: 104,
                        render: (row) => (
                          <span className="tnum">{fmtMeters(row.md, 1)}</span>
                        ),
                      },
                      { key: 'channel', header: 'Channel' },
                      { key: 'detector', header: 'Detector', width: 88 },
                      {
                        key: 'severity',
                        header: 'Band',
                        width: 96,
                        render: (row) => (
                          <span
                            className="inline-flex items-center gap-1.5"
                            style={{ color: severityFillVar(row.severity) }}
                          >
                            <span
                              aria-hidden
                              className="size-1.5 rounded-full"
                              style={{ background: severityFillVar(row.severity) }}
                            />
                            {humanizeEnum(row.severity)}
                          </span>
                        ),
                      },
                    ]}
                    rows={alertItems}
                  />
                </>
              ) : null}
            </ResourceState>
          </Disclosure>
        </div>
      </ResourceState>
    </div>
  )
}
