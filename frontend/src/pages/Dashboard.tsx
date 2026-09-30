import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Api } from "@/api/client";
import type {
  AlertBrief,
  AlertDetail,
  EventDetail,
  OffsetWell,
  Well,
} from "@/api/types";
import { useAsyncData } from "@/lib/useAsyncData";
import { useElementWidth } from "@/lib/useElementWidth";
import {
  bandDotClass,
  bandTone,
  eventSeverityDotClass,
  fmtDate,
  fmtKm,
  fmtMeters,
  fmtMetersShort,
  fmtNumber,
  fmtPercent,
  humanizeEnum,
  relevanceTone,
} from "@/lib/format";
import { useApp } from "@/store/useApp";
import {
  Badge,
  Callout,
  DataTable,
  DepthAxis,
  Disclosure,
  EmptyState,
  KeyStat,
  LineIcon,
  SectionCard,
  ScreenHeader,
  Skeleton,
  KeyValueGrid,
  StatRow,
  Toolbar,
  WhyFactors,
  controlClass,
  cx,
  ghostButtonClass,
  actionButtonClass,
  Meter,
  type DataTableColumn,
  type IconName,
} from "@/components/ui";
import {
  ErrorPanel,
  LABEL,
  ResourceState,
  SyntheticStrip,
  niceStep,
} from "./_shared";
import {
  AlertEvidence,
  FormationName,
  PROXY_FORMATION_REASON,
  PROXY_POSITION_REASON,
  ProxyTag,
  SliceNote,
  isProxyFormation,
  isProxyPosition,
  isTelemetryAlert,
  useBounded,
} from "./_calm";

const RADIUS_CHOICES = [4, 8, 12];

/**
 * Screen 1 — Active Well Dashboard.
 *
 * Anatomy (UX contract §4.1): one hero — "Needs attention now" — then a
 * two-panel row, then reference data in collapsed disclosures. The former
 * "Jump to" card is a compact icon row in the header, and the loose method
 * prose is gone; the shell already carries the data-provenance banner.
 *
 * Real-data corrections this screen has to make honest:
 *  - the active well is `15/9-F-9A`, whose id contains a slash, so every link
 *    into it is percent-encoded with `encodeURIComponent`;
 *  - its formation column and coordinates are a labelled 15/9-block proxy, so
 *    the formation readout carries the proxy tag;
 *  - alerts come from live telemetry, so `supporting_well_count` is honestly 0
 *    and the evidence line names telemetry rather than a missing datum;
 *  - the cluster now holds 1,250 events, so every table here is bounded.
 *
 * Every value rendered comes from the API: the depth/TVD curve from
 * `well.trajectory`, the open alerts from `/alerts`, the offset table from
 * `/wells/{id}/nearby` with its own `factors[]`. No score is recomputed here.
 */
export function Dashboard() {
  const {
    meta,
    currentWell,
    currentWellLoading,
    currentWellId,
    openEvidence,
    openDecision,
    selectedOffsetWellIds,
    toggleOffsetWell,
    relevanceConfig,
    notify,
  } = useApp();

  /* The dataset label rides in the header meta instead of a second banner:
     the app shell already carries the global provenance strip. */
  const datasetMeta = meta ? <SyntheticStrip label={meta.dataset_label} /> : null;
  const [radiusKm, setRadiusKm] = useState(relevanceConfig?.radius_km ?? 8);
  /* `15/9-F-9A` contains a slash. Every cross-screen link must percent-encode
     it or the router reads `15` as a path segment. */
  const wellParam = encodeURIComponent(currentWellId);

  const overview = useAsyncData(
    async (signal) => {
      if (!currentWellId) return null;
      const [nearby, alerts, events] = await Promise.all([
        Api.nearby(currentWellId, { radius_km: radiusKm }, signal),
        Api.alerts({ well_id: currentWellId, status: "OPEN" }, signal),
        /* The cluster holds 1,250 events. Fetch a deliberate page and bound it
           on screen — the screen must never depend on the API's default page. */
        Api.events(
          { near_well_id: currentWellId, radius_km: radiusKm, limit: 120 },
          signal,
        ),
      ]);
      return { nearby, alerts, events };
    },
    [currentWellId, radiusKm],
  );

  const openAlerts = useMemo(
    () =>
      (overview.data?.alerts.items ?? [])
        .slice()
        .sort((a, b) => b.risk_score - a.risk_score),
    [overview.data],
  );

  /* Only the highest open alert needs its full record for the hero; the
     collapsed list below uses the brief's own headline. */
  const leadAlert = openAlerts[0] ?? null;

  const alertDetails = useAsyncData<AlertDetail[]>(
    async (signal) => {
      if (!leadAlert) return [];
      const detail = await Api.alert(leadAlert.id, signal).catch(() => null);
      return detail ? [detail] : [];
    },
    [leadAlert?.id],
  );

  const leadAlertDetail = alertDetails.data?.[0] ?? null;

  const rankedOffsets = useMemo(
    () =>
      (overview.data?.nearby.items ?? [])
        .slice()
        .sort((a, b) => b.relevance_score - a.relevance_score),
    [overview.data],
  );
  const topOffsetWells = useBounded(rankedOffsets, 6, 6);

  const well = currentWell;
  const events = overview.data?.events.items ?? [];
  const alertCount = openAlerts.length;
  const nearbyCount = overview.data?.nearby.items.length ?? 0;

  const eventsTotal = overview.data?.events.total ?? 0;
  const proxyFormation = isProxyFormation(well);
  const proxyPosition = isProxyPosition(well);
  const clusterEvents = useBounded(events, 8, 12);

  return (
    <div className="flex flex-col gap-3 p-3">
      <ScreenHeader
        title="Active well dashboard"
        subtitle="Where the bit is, the one thing that needs a decision, and the offsets worth knowing about."
        meta={datasetMeta}
        actions={<JumpRow nearbyCount={nearbyCount} alertCount={alertCount} />}
      />

      {overview.error ? <ErrorPanel message={overview.error} onRetry={overview.reload} /> : null}

      {/* ================================ HERO ================================ */}
      {/* The screen's one question: what is the bit doing, and is anything
          open on it. Four readouts, then the single highest open alert. The
          well's `status_note` is a 60-word paragraph — it belongs in the
          record disclosure, not under a number the reader is trying to scan. */}
      <SectionCard
        title="Needs attention now"
        description={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-medium text-fg-strong">{well?.name ?? "Current well"}</span>
            <span aria-hidden>·</span>
            <span className="tnum">{well ? fmtMeters(well.current_tvd) : "—"} TVD</span>
            <span aria-hidden>·</span>
            <FormationName well={well} />
          </span>
        }
        actions={
          <>
            <button
              type="button"
              className={actionButtonClass}
              disabled={!leadAlert}
              onClick={() => leadAlert && openDecision(leadAlert.id)}
            >
              <LineIcon name="alert" size={12} />
              View decision
            </button>
            <Link
              to={`/offset-intelligence/replay?well=${wellParam}`}
              className={ghostButtonClass}
            >
              <LineIcon name="replay" size={12} />
              Replay offset wells
            </Link>
          </>
        }
      >
        {currentWellLoading && !well ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-56" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : (
          <>
            <StatRow>
              <KeyStat
                label="Current depth"
                value={well ? fmtNumber(well.current_depth_md) : "—"}
                unit="m MD"
                hint={well ? `TD ${fmtMeters(well.total_depth_md)}` : undefined}
              />
              <KeyStat
                label="Current TVD"
                value={well ? fmtNumber(well.current_tvd) : "—"}
                unit="m TVD"
                tone="warning"
                hint={well ? `Hole ${well.mud_system.toLowerCase()}` : undefined}
              />
              <KeyStat
                label="Formation"
                value={<FormationName well={well} />}
                hint={
                  well
                    ? `${fmtMeters(well.current_formation.top_depth)}–${fmtMeters(well.current_formation.bottom_depth)}${proxyFormation ? " · derived" : ""}`
                    : undefined
                }
              />
              <KeyStat
                label="Open alerts"
                value={fmtNumber(alertCount, 0)}
                tone={alertCount > 0 ? "critical" : "success"}
                hint={
                  well?.top_alert_severity
                    ? `Highest ${humanizeEnum(well.top_alert_severity)}`
                    : "None open"
                }
              />
            </StatRow>

            {/* The derived-data correction belongs directly under the numbers it
                qualifies, not only in the shell footer — a reader who lands on
                this screen first must still see it. */}
            {proxyFormation || proxyPosition ? (
              <p className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs leading-relaxed text-fg-subtle">
                <ProxyTag>15/9 proxy</ProxyTag>
                <span>
                  {proxyFormation ? PROXY_FORMATION_REASON : PROXY_POSITION_REASON}
                </span>
              </p>
            ) : null}

            <div className="mt-3">
              {leadAlert ? (
                <LeadAlertCard
                  alert={leadAlert}
                  detail={leadAlertDetail}
                  loadingDetail={alertDetails.loading}
                />
              ) : (
                <Callout tone="success" title="No open alerts on this well">
                  The rule engine returned nothing open inside its configured thresholds
                  for {well?.name ?? "the current well"}.
                </Callout>
              )}
            </div>
          </>
        )}
      </SectionCard>

      {/* ========================== second row: 7 / 5 ========================= */}
      {/* The only other thing visible without a click. The curve answers "how
          far have we drilled and how vertical is the hole"; the ranking answers
          "which neighbours matter". They pair because both are read once and
          neither is reference data. */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
        <SectionCard
          className="xl:col-span-7"
          title="Depth vs true vertical depth"
          description="Trajectory samples from the API, on a shared depth ruler"
          actions={
            <span className="tnum text-2xs text-fg-muted">
              {well ? `${well.trajectory.length} samples` : "—"}
            </span>
          }
          dense
        >
          {currentWellLoading && !well ? (
            <Skeleton className="h-[220px] w-full" />
          ) : (
            <DepthTvdCurve well={well} />
          )}
        </SectionCard>

        <SectionCard
          className="xl:col-span-5"
          title="Top offset wells"
          description={`Engine-scored within ${radiusKm} km · click a row to load it into the replay`}
          actions={
            <Link
              to={`/offset-intelligence/map?well=${wellParam}`}
              className={ghostButtonClass}
            >
              <LineIcon name="map" size={12} /> Open map
            </Link>
          }
          dense
        >
          <OffsetWellTable
            rows={topOffsetWells.visible}
            loading={overview.loading}
            selectedIds={selectedOffsetWellIds}
            onToggle={(row) => {
              toggleOffsetWell(row.well.id);
              notify(
                `${row.well.name} relevance ${fmtPercent(row.relevance_score, 0)} — added to the replay selection`,
                "info",
              );
            }}
          />
          <SliceNote
            className="mt-2"
            shown={topOffsetWells.shown}
            total={nearbyCount}
            noun="offset wells"
            onMore={topOffsetWells.more}
          />
        </SectionCard>
      </div>

      {/* ========================= one filter bar ============================= */}
      {/* One control, one sentence. The radius governs the offset ranking and
          the cluster event list and nothing else. */}
      <Toolbar className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <label className="flex items-center gap-2">
          <span className={LABEL}>Nearby radius</span>
          <select
            aria-label="Nearby radius"
            className={controlClass}
            value={radiusKm}
            onChange={(event) => setRadiusKm(Number(event.target.value))}
          >
            {RADIUS_CHOICES.map((choice) => (
              <option key={choice} value={choice}>
                {choice} km
              </option>
            ))}
          </select>
        </label>
        <p className="tnum text-2xs text-fg-subtle">
          {`${nearbyCount} offset wells · ${eventsTotal} cluster events inside ${radiusKm} km. Relevance is an engine heuristic.`}
        </p>
      </Toolbar>

      {/* ==================== reference level, all closed ===================== */}
      {/* Three disclosures side by side. This is the LAST level on the screen —
          nothing nests below it, so the working view stays hero + one row. */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        <Disclosure
          summary="Open alerts"
          badge={<Badge tone={alertCount > 0 ? "warning" : "muted"}>{alertCount}</Badge>}
        >
          <ResourceState loading={overview.loading && alertCount === 0} error={null}>
            {alertCount === 0 ? (
              <p className="py-3 text-xs text-fg-subtle">
                No open alerts for this well in the current record.
              </p>
            ) : (
              <ul className="divide-y divide-line">
                {openAlerts.map((alert) => (
                  <OpenAlertRow key={alert.id} alert={alert} onOpen={openDecision} />
                ))}
              </ul>
            )}
          </ResourceState>
        </Disclosure>

        <Disclosure summary="Current well record">
          {well ? (
            <KeyValueGrid
              columns={2}
              items={[
                { label: "Spud date", value: fmtDate(well.spud_date), mono: true },
                { label: "Water depth", value: fmtMeters(well.water_depth_m), mono: true },
                { label: "Total depth", value: fmtMeters(well.total_depth_md), mono: true },
                { label: "Rig", value: well.rig ?? "—" },
                { label: "Mud system", value: well.mud_system },
                { label: "Section / bit", value: well.operating_context?.section_size ?? "—" },
                {
                  label: "Coordinates",
                  value: `${well.latitude.toFixed(3)} N, ${well.longitude.toFixed(3)} E`,
                  mono: true,
                },
                {
                  label: "Operator",
                  value: well.operator,
                },
              ]}
            />
          ) : (
            <p className="py-3 text-xs text-fg-subtle">No active well record loaded.</p>
          )}
          {well?.operating_context?.status_note ? (
            <p className="mt-3 border-t border-line pt-2 text-2xs leading-relaxed text-fg-subtle">
              {well.operating_context.status_note}
            </p>
          ) : null}
        </Disclosure>

        <Disclosure
          summary="Offset cluster events"
          badge={
            <span className="tnum text-2xs text-fg-subtle">
              {clusterEvents.shown} of {eventsTotal}
            </span>
          }
        >
          <RecentEventsTable
            events={clusterEvents.visible}
            loading={overview.loading}
            onOpen={openEvidence}
            currentWellId={currentWellId}
          />
          <SliceNote
            className="mt-2"
            shown={clusterEvents.shown}
            total={eventsTotal}
            noun="cluster events"
            onMore={clusterEvents.more}
          />
        </Disclosure>
      </div>
    </div>
  );
}


/* ------------------------------------------------------------------ *
 * Header jump row
 * ------------------------------------------------------------------ */

function JumpRow({
  nearbyCount,
  alertCount,
}: {
  nearbyCount: number;
  alertCount: number;
}) {
  const { currentWellId } = useApp();
  /* `15/9-F-9A` has a slash in it: unencoded, the router would read `15` as a
     path segment and drop the well. Every deep link encodes it. */
  const wellParam = encodeURIComponent(currentWellId);
  const links: { to: string; label: string; icon: IconName; count?: string }[] = [
    {
      to: `/offset-intelligence/map?well=${wellParam}`,
      label: "Offset map",
      icon: "map",
      count: `${nearbyCount}`,
    },
    {
      to: `/offset-intelligence/replay?well=${wellParam}`,
      label: "Offset replay",
      icon: "replay",
    },
    { to: `/alerts?well=${wellParam}`, label: "Alerts", icon: "alert", count: `${alertCount}` },
    { to: `/events/timeline?well=${wellParam}`, label: "Timeline", icon: "timeline" },
    { to: `/search?well=${wellParam}`, label: "Search", icon: "search" },
  ];

  return (
    <nav aria-label="Jump to" className="flex flex-wrap items-center gap-1.5">
      {links.map((link) => (
        <Link
          key={link.to}
          to={link.to}
          title={link.label}
          className={cx(
            ghostButtonClass,
            "gap-1.5 px-2 normal-case tracking-normal",
          )}
        >
          <LineIcon name={link.icon} size={13} className="text-accent" />
          <span className="text-2xs font-medium text-fg-strong">{link.label}</span>
          {link.count !== undefined ? (
            <span className="tnum text-2xs text-fg-subtle">{link.count}</span>
          ) : null}
        </Link>
      ))}
    </nav>
  );
}

/* ------------------------------------------------------------------ *
 * Hero: the single highest open alert
 * ------------------------------------------------------------------ */

function LeadAlertCard({
  alert,
  detail,
  loadingDetail,
}: {
  alert: AlertBrief;
  detail: AlertDetail | null;
  loadingDetail: boolean;
}) {
  return (
    <Callout
      tone={bandTone(alert.severity_band)}
      title={
        <span className="flex flex-wrap items-center gap-1.5">
          <span
            className={cx("size-2 shrink-0 rounded-full", bandDotClass(alert.severity_band))}
          />
          <span className="tnum text-2xs font-normal text-fg-muted">{alert.id}</span>
          <span className="text-sm font-semibold">{alert.title}</span>
          <Badge tone={bandTone(alert.severity_band)}>
            {humanizeEnum(alert.severity_band)}
          </Badge>
          <Badge tone="muted">risk {fmtNumber(alert.risk_score, 2)}</Badge>
        </span>
      }
    >
      <p>{alert.headline}</p>
      {/* The evidence line names the source. A telemetry alert genuinely has
          zero supporting offset wells — the Directorate publishes no
          per-wellbore DDR attribution — so that is stated, not shown as "0". */}
      <p className="tnum mt-1 text-2xs text-fg-muted">
        {(() => {
          const { top_tvd: top, bottom_tvd: bottom } = alert.interval
          // A single-sample alarm collapses to a zero-width interval, which
          // reads as a bug. Say "at <depth>" instead.
          const interval =
            Math.abs(bottom - top) < 1
              ? `at ${fmtMeters(top)}`
              : `${fmtMeters(top)}–${fmtMeters(bottom)}`
          return `${interval} TVD in ${alert.formation} · current TVD ${fmtMeters(alert.current_tvd)} · ${fmtMeters(alert.distance_to_interval_m)} from it`
        })()}
      </p>
      <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
        <AlertEvidence alert={alert} />
        {isTelemetryAlert(alert) ? <ProxyTag>Telemetry</ProxyTag> : null}
      </div>
      {loadingDetail ? (
        <Skeleton className="mt-2 h-3 w-64" />
      ) : detail ? (
        <WhyFactors
          factors={detail.risk_factors}
          title="Why this alert"
          defaultOpen={false}
        />
      ) : null}
    </Callout>
  );
}

function OpenAlertRow({
  alert,
  onOpen,
}: {
  alert: AlertBrief;
  onOpen: (alertId: string) => void;
}) {
  return (
    <li className="flex flex-wrap items-center gap-x-2 gap-y-1 py-1.5">
      <span className={cx("size-2 shrink-0 rounded-full", bandDotClass(alert.severity_band))} />
      <span className="tnum text-2xs text-fg-subtle">{alert.id}</span>
      <span className="text-xs font-semibold text-fg-strong">{alert.title}</span>
      <Badge tone={bandTone(alert.severity_band)}>{humanizeEnum(alert.severity_band)}</Badge>
      <Badge tone="muted">risk {fmtNumber(alert.risk_score, 2)}</Badge>
      <AlertEvidence alert={alert} className="basis-full" />
      <button
        type="button"
        onClick={() => onOpen(alert.id)}
        className="ml-auto text-2xs font-semibold text-accent underline underline-offset-2"
      >
        Open decision
      </button>
    </li>
  );
}

/* ------------------------------------------------------------------ *
 * Depth vs TVD curve (hand-built SVG, no chart library)
 * ------------------------------------------------------------------ */

const CURVE_HEIGHT = 220;
const CURVE_PAD = { top: 12, right: 12, bottom: 20 };
/** DepthAxis renders at `w-14`; the plot must leave exactly that much room. */
const CURVE_AXIS_W = 56;

function DepthTvdCurve({
  well,
}: {
  well: Well | null;
}) {
  const { ref, width } = useElementWidth<HTMLDivElement>(900);

  const geometry = useMemo(() => {
    if (!well || well.trajectory.length < 2) return null;
    // X domain is the drilled hole, not total depth: the trajectory stops at
    // hole bottom, so plotting against TD would stretch the curve into an
    // empty right-hand region and imply surveys we do not have.
    const holeBottomMd = Math.max(
      well.current_depth_md,
      well.trajectory[well.trajectory.length - 1].md,
      1,
    );
    const maxMd = holeBottomMd;
    const maxTvd = Math.max(
      well.current_tvd,
      ...well.trajectory.map((point) => point.tvd),
      1,
    );
    // The measured width covers the DepthAxis (56px) plus the container's own
    // padding and gap; subtracting only the right pad let the SVG overflow the
    // card at 1024px and pushed the NOW label off the edge.
    const plotWidth = Math.max(120, width - CURVE_AXIS_W - CURVE_PAD.right);
    const plotHeight = CURVE_HEIGHT - CURVE_PAD.top - CURVE_PAD.bottom;

    const x = (md: number) => (md / maxMd) * plotWidth;
    // Depth grows downward, matching the DepthAxis ruler beside it: 0 m at the
    // top, hole bottom at the bottom. Inverting this makes a shallower hole look
    // deeper, which is the one thing a drilling engineer cannot tolerate.
    const y = (tvd: number) => CURVE_PAD.top + (tvd / maxTvd) * plotHeight;

    const line = well.trajectory
      .map((point) => `${x(point.md).toFixed(1)},${y(point.tvd).toFixed(1)}`)
      .join(" ");
    // Area closes against the surface (depth 0) at the top, not the base.
    const area = `0,${CURVE_PAD.top.toFixed(1)} ${line} ${x(maxMd).toFixed(1)},${CURVE_PAD.top.toFixed(1)}`;

    const gridStep = niceStep(maxTvd / 5);
    const gridValues: number[] = [];
    for (let value = 0; value <= maxTvd; value += gridStep)
      gridValues.push(value);

    const mdStep = niceStep(maxMd / 5);
    const mdValues: number[] = [];
    for (let value = 0; value <= maxMd; value += mdStep) mdValues.push(value);

    const formationTop = Math.min(well.current_formation.top_depth, maxTvd);
    const formationBottom = Math.min(
      well.current_formation.bottom_depth,
      maxTvd,
    );

    return {
      maxMd,
      maxTvd,
      plotWidth,
      plotHeight,
      line,
      area,
      gridValues,
      mdValues,
      x,
      y,
      formationTop,
      formationBottom,
    };
  }, [well, width]);

  return (
    <div ref={ref} className="plot-surface flex gap-1 p-2">
      {geometry ? (
        <>
          <DepthAxis
            topM={0}
            bottomM={geometry.maxTvd}
            className="h-[220px]"
            labels={geometry.gridValues}
          />
          <svg
            width={geometry.plotWidth}
            height={CURVE_HEIGHT}
            viewBox={`0 0 ${geometry.plotWidth} ${CURVE_HEIGHT}`}
            className="shrink-0"
            role="img"
            aria-label={`Measured depth versus true vertical depth for the current well. Current position ${well?.current_tvd ?? 0} metres TVD.`}
          >
            {/* current formation band */}
            {geometry.formationBottom > geometry.formationTop && (
              <rect
                x={0}
                y={geometry.y(geometry.formationBottom)}
                width={geometry.plotWidth}
                height={Math.max(
                  1,
                  geometry.y(geometry.formationTop) -
                    geometry.y(geometry.formationBottom),
                )}
                className="fill-[var(--accent-soft)] stroke-[var(--accent-line)]"
                strokeWidth={0.6}
              />
            )}

            {/* TVD gridlines */}
            {geometry.gridValues.map((value) => (
              <g key={`g-${value}`}>
                <line
                  x1={0}
                  x2={geometry.plotWidth}
                  y1={geometry.y(value)}
                  y2={geometry.y(value)}
                  className="stroke-[var(--grid)]"
                  strokeWidth={1}
                />
              </g>
            ))}

            {/* MD gridlines — same token as TVD, lighter, so the two axes do
                not read as equally important. */}
            {geometry.mdValues.map((value) => (
              <g key={`m-${value}`}>
                <line
                  x1={geometry.x(value)}
                  x2={geometry.x(value)}
                  y1={CURVE_PAD.top}
                  y2={CURVE_PAD.top + geometry.plotHeight}
                  className="stroke-[var(--grid)]"
                  strokeWidth={1}
                  opacity={0.6}
                />
                {value > 0 && (
                  <text
                    x={Math.min(geometry.x(value), geometry.plotWidth - 22)}
                    y={CURVE_HEIGHT - 6}
                    fontSize={9}
                    className="fill-[var(--fg-subtle)]"
                    textAnchor="middle"
                  >
                    {Math.round(value).toLocaleString("en-US")}
                  </text>
                )}
              </g>
            ))}

            <polygon
              points={geometry.area}
              className="fill-[var(--accent-soft)]"
              // Light enough to stay a backdrop: the curve and the marker are
              // the message, not the wedge behind them.
              opacity={0.45}
            />
            {/* The curve is the one mark that must read on both themes, so it
                takes the accent token: in dark, --chrome-2 (#101d31) sits
                almost on top of --plot-bg and the curve disappears. */}
            <polyline
              points={geometry.line}
              fill="none"
              className="stroke-[var(--accent)]"
              strokeWidth={1.8}
              strokeLinejoin="round"
              strokeLinecap="round"
            />

            {/* MD axis caption (the bare 0 would collide with the depth ruler) */}
            <text
              x={0}
              y={CURVE_HEIGHT - 17}
              fontSize={9}
              fontWeight={600}
              className="fill-[var(--fg-muted)]"
            >
              MD (m)
            </text>
            {/* No corner caption: the MD ticks already label the scale, the NOW
                marker states the current MD, and total depth is in the Current
                depth stat directly above. It only collided with the 1,500 tick. */}

            {/* current position marker */}
            {well && (
              <g>
                <line
                  x1={geometry.x(well.current_depth_md)}
                  x2={geometry.x(well.current_depth_md)}
                  y1={CURVE_PAD.top}
                  y2={CURVE_PAD.top + geometry.plotHeight}
                  className="stroke-[var(--warn)]"
                  strokeWidth={1}
                  strokeDasharray="3 3"
                />
                <line
                  x1={0}
                  x2={geometry.plotWidth}
                  y1={geometry.y(well.current_tvd)}
                  y2={geometry.y(well.current_tvd)}
                  className="stroke-[var(--warn)]"
                  strokeWidth={1}
                  strokeDasharray="3 3"
                />
                <circle
                  cx={geometry.x(well.current_depth_md)}
                  cy={geometry.y(well.current_tvd)}
                  r={3.5}
                  className="fill-[var(--warn)]"
                  stroke="var(--plot-bg)"
                  strokeWidth={1.2}
                />
                {/* The marker sits at hole bottom, i.e. the right edge, so the
                    label flips to the inside of the plot instead of clipping. */}
                {(() => {
                  const markerX = geometry.x(well.current_depth_md);
                  const nearRightEdge = markerX > geometry.plotWidth - 150;
                  return (
                    <text
                      x={nearRightEdge ? Math.max(markerX - 8, 0) : markerX + 6}
                      // Clear of the x-axis captions below the plot, which sit at
                      // y 203/214; the marker at hole bottom would otherwise print
                      // the "NOW" label straight through them.
                      y={Math.max(geometry.y(well.current_tvd) - 16, CURVE_PAD.top + 11)}
                      fontSize={9.5}
                      fontWeight={600}
                      className="fill-[var(--warn)]"
                      textAnchor={nearRightEdge ? "end" : "start"}
                    >
                      {`NOW · MD ${fmtNumber(well.current_depth_md)} m / TVD ${fmtNumber(well.current_tvd)} m`}
                    </text>
                  );
                })()}
                {/* Anchored to the TOP of the formation band, which is where the
                    band starts now that depth increases downward. */}
                <text
                  x={4}
                  y={Math.min(
                    Math.max(geometry.y(well.current_formation.top_depth) + 11, CURVE_PAD.top + 9),
                    CURVE_PAD.top + geometry.plotHeight - 4,
                  )}
                  fontSize={9}
                  className="fill-[var(--accent)]"
                >
                  {well.current_formation.name}
                </text>
              </g>
            )}
          </svg>
        </>
      ) : (
        <EmptyState
          title="No trajectory samples"
          hint="The API returned fewer than two trajectory points for this well, so no depth curve can be drawn."
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Offset wells table
 * ------------------------------------------------------------------ */

function OffsetWellTable({
  rows,
  loading,
  selectedIds,
  onToggle,
}: {
  rows: OffsetWell[];
  loading: boolean;
  selectedIds: string[];
  onToggle: (row: OffsetWell) => void;
}) {
  const columns: DataTableColumn<OffsetWell>[] = [
    {
      key: "well",
      header: "Offset well",
      render: (row) => (
        <span className="flex items-center gap-1.5">
          <span className="font-semibold text-fg-strong">{row.well.name}</span>
          {selectedIds.includes(row.well.id) && (
            <Badge tone="info">In replay</Badge>
          )}
        </span>
      ),
    },
    {
      key: "distance",
      header: "Distance",
      align: "right",
      width: 84,
      render: (row) => <span className="tnum">{fmtKm(row.distance_km)}</span>,
    },
    {
      key: "relevance",
      header: "Relevance",
      width: 118,
      render: (row) => (
        <span className="flex items-center gap-1.5">
          {/* Relevance is not a severity: one accent bar whose length carries the
              score, so red stays exclusive to a genuine CRITICAL alert. The
              band chip beside it already encodes the level. */}
          <Meter
            value={row.relevance_score}
            fillClass="bg-accent"
            height={5}
            label={`${row.well.name} relevance ${fmtPercent(row.relevance_score, 0)}`}
          />
          <span className="tnum w-8 text-right text-2xs text-fg-muted">
            {fmtPercent(row.relevance_score, 0)}
          </span>
        </span>
      ),
    },
    {
      key: "band",
      header: "Band",
      width: 82,
      render: (row) => (
        <Badge tone={relevanceTone(row.relevance_score)}>
          {humanizeEnum(row.relevance_band)}
        </Badge>
      ),
    },
    {
      // Real offset records carry 0 events and 0 documents of their own, so an
      // "engine evidence" column read as four empty numbers on every row. The
      // full factors stay one click away on the offset map's well detail.
      key: "why",
      header: "Factors",
      width: 84,
      render: (row) => (
        <span className="tnum text-2xs text-fg-muted">{row.factors.length} scored</span>
      ),
    },
  ];

  if (loading && rows.length === 0) {
    return (
      <div className="space-y-2 p-3">
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-11/12" />
        <Skeleton className="h-3 w-10/12" />
      </div>
    );
  }

  return (
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(row) => row.well.id}
      onRowClick={onToggle}
      selectedKey={undefined}
      empty={
        <EmptyState
          title="No offset wells in radius"
          hint="Widen the nearby radius, or check that the dataset has been loaded."
        />
      }
    />
  );
}

/* ------------------------------------------------------------------ *
 * Recent events table
 * ------------------------------------------------------------------ */

function RecentEventsTable({
  events,
  loading,
  onOpen,
  currentWellId,
}: {
  events: EventDetail[];
  loading: boolean;
  onOpen: (eventId: string) => void;
  currentWellId: string;
}) {
  const columns: DataTableColumn<EventDetail>[] = [
    {
      key: "occurred",
      header: "Date",
      width: 104,
      render: (row) => <span className="tnum">{fmtDate(row.occurred_at)}</span>,
    },
    {
      key: "well",
      header: "Well",
      width: 108,
      render: (row) => (
        <span className="flex items-center gap-1.5">
          <span className="text-fg-strong">{row.well_name}</span>
          {row.well_id === currentWellId && <Badge tone="info">Current</Badge>}
        </span>
      ),
    },
    {
      key: "event",
      header: "Event",
      width: 148,
      render: (row) => <span className="text-fg-strong">{row.event_label}</span>,
    },
    {
      key: "md",
      header: "MD",
      align: "right",
      width: 78,
      render: (row) => <span className="tnum">{fmtMetersShort(row.md)}</span>,
    },
    {
      key: "tvd",
      header: "TVD",
      align: "right",
      width: 78,
      render: (row) => <span className="tnum">{fmtMetersShort(row.tvd)}</span>,
    },
    {
      key: "formation",
      header: "Formation",
      width: 116,
      render: (row) => (
        /* The event's own formation string comes from the extraction, not from
           the current well's proxy column, so no proxy tag belongs here. */
        <span className="text-fg">{row.formation || "—"}</span>
      ),
    },
    {
      key: "severity",
      header: "Severity",
      width: 100,
      render: (row) => (
        <span className="flex items-center gap-1.5">
          <span
            className={cx("size-1.5 rounded-full", eventSeverityDotClass(row.severity))}
          />
          <span className="text-xs">{humanizeEnum(row.severity)}</span>
        </span>
      ),
    },
    {
      key: "evidence",
      header: "Evidence",
      align: "right",
      width: 82,
      render: (row) => <span className="tnum">{row.evidence_count}</span>,
    },
    {
      key: "actions",
      header: "",
      width: 104,
      align: "right",
      render: (row) => (
        <button
          type="button"
          onClick={() => onOpen(row.id)}
          className="text-2xs font-semibold text-accent underline underline-offset-2"
        >
          Audit chain
        </button>
      ),
    },
  ];

  if (loading && events.length === 0) {
    return (
      <div className="space-y-2 p-3">
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-9/12" />
      </div>
    );
  }

  return (
    <DataTable
      columns={columns}
      rows={events}
      rowKey={(row) => row.id}
      onRowClick={(row) => onOpen(row.id)}
      empty={
        <EmptyState
          title="No events in the offset cluster"
          hint="Nothing was returned for this well inside the selected radius."
        />
      }
    />
  );
}


