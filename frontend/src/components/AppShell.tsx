import { useState, type ReactNode } from 'react'
import { Link, NavLink } from 'react-router-dom'
import type { Tone } from './primitives'
import { Badge, cx } from './primitives'
import { DecisionPanel } from './DecisionPanel'
import { EvidenceDrawer } from './EvidenceDrawer'
import { LineIcon, type IconName } from './LineIcon'
import { bandTone, fmtKm, fmtMeters, humanizeEnum, wellStatusTone } from '@/lib/format'
import { Api } from '@/api/client'
import type { NearbyResponse } from '@/api/types'
import { useAsyncData } from '@/lib/useAsyncData'
import { STATIC_DATA } from '@/api/client'
import { useApp } from '@/store/useApp'
import { isProxyFormation, isProxyPosition, ProxyTag } from '@/pages/_calm'

interface NavItem {
  to: string
  label: string
  icon: IconName
  end?: boolean
}

interface NavGroup {
  heading: string
  items: NavItem[]
}

/** Information architecture — fixed by the prototype spec, plus System. */
const NAV: NavGroup[] = [
  {
    heading: 'Active Well',
    items: [{ to: '/', label: 'Dashboard', icon: 'well', end: true }],
  },
  {
    heading: 'Detection',
    items: [
      { to: '/telemetry', label: 'Telemetry Monitor', icon: 'layers' },
      { to: '/validation', label: 'Backtest & Validation', icon: 'check' },
    ],
  },
  {
    heading: 'Offset Intelligence',
    items: [
      { to: '/offset-intelligence/map', label: 'Offset Map', icon: 'map' },
      { to: '/offset-intelligence/replay', label: 'Offset Replay', icon: 'replay' },
    ],
  },
  {
    heading: 'Events',
    items: [{ to: '/events/timeline', label: 'Depth Timeline', icon: 'timeline' }],
  },
  {
    heading: 'Risk',
    items: [{ to: '/alerts', label: 'Alerts', icon: 'alert' }],
  },
  {
    heading: 'Knowledge',
    items: [
      { to: '/search', label: 'Knowledge Search', icon: 'search' },
      { to: '/documents', label: 'Evidence & Documents', icon: 'document' },
      { to: '/relevance-method', label: 'Relevance Method', icon: 'sliders' },
      { to: '/graph', label: 'Graph Explorer', icon: 'compass' },
    ],
  },
  {
    heading: 'System',
    items: [{ to: '/settings', label: 'Settings', icon: 'settings' }],
  },
]

export function AppShell({ children }: { children: ReactNode }) {
  const {
    meta,
    wells,
    currentWell,
    currentWellId,
    setCurrentWell,
    error,
    apiOk,
    toast,
    dismissToast,
    evidenceEventId,
    closeEvidence,
    decisionAlertId,
    closeDecision,
    refresh,
    relevanceConfig,
    resolvedTheme,
    toggleTheme,
  } = useApp()

  // The context bar must agree with the screens: "offset wells" is the count
  // inside the active relevance radius, not the dataset-wide relation count.
  const radiusKm = relevanceConfig?.radius_km ?? 8
  const { data: nearby } = useAsyncData<NearbyResponse>(
    (signal) => Api.nearby(currentWellId, { radius_km: radiusKm }, signal),
    [currentWellId, radiusKm],
  )

  const nextTheme = resolvedTheme === 'dark' ? 'light' : 'dark'

  // The nav collapses to an icon rail so the plot-heavy screens get the full
  // width at 1024px. Persisted because it is a workspace preference, not a
  // transient control.
  const [navCollapsed, setNavCollapsed] = useState(
    () => window.localStorage.getItem('pravah.nav.collapsed') === '1',
  )
  const toggleNav = () =>
    setNavCollapsed((collapsed) => {
      window.localStorage.setItem('pravah.nav.collapsed', collapsed ? '0' : '1')
      return !collapsed
    })

  const formationIsProxy = isProxyFormation(currentWell)
  const positionIsProxy = isProxyPosition(currentWell)

  return (
    <div className="flex h-screen min-h-[640px] flex-col overflow-hidden bg-surface text-fg">
      {/* ------------------------------ top bar ------------------------------ */}
      <header className="flex h-11 shrink-0 items-center gap-3 border-b border-chrome-2 bg-chrome px-3 text-chrome-fg">
        <div className="flex items-center gap-2">
          <span className="flex size-6 items-center justify-center rounded-sm border border-chrome-2 bg-chrome-2">
            <LineIcon name="layers" size={15} className="text-accent" />
          </span>
          <div className="leading-none">
            <p className="text-sm font-semibold tracking-tight">Pravah</p>
            <p className="text-2xs text-chrome-muted">Near-Well Intelligence</p>
          </div>
        </div>

        {/* One dataset count line, no badge: the provenance strip below already
            carries the label, so a second badge here would be the same fact
            shouted twice. */}
        {meta && (
          <p className="tnum ml-3 hidden border-l border-chrome-2 pl-3 text-2xs text-chrome-muted lg:block">
            {meta.counts.wells} wells · {meta.counts.events} events · {meta.counts.alerts} alerts
          </p>
        )}

        <div className="ml-auto flex items-center gap-2">
          {apiOk === false && (
            <button
              type="button"
              onClick={refresh}
              className="inline-flex h-7 items-center gap-1.5 rounded-sm border border-crit bg-crit px-2 text-2xs font-semibold uppercase tracking-[0.06em] text-crit-soft"
            >
              <LineIcon name="refresh" size={12} /> API offline — retry
            </button>
          )}
          <button
            type="button"
            onClick={toggleTheme}
            title={`Appearance: ${resolvedTheme === 'dark' ? 'Dark' : 'Light'} — switch to ${nextTheme === 'dark' ? 'Dark' : 'Light'}`}
            aria-label={`Switch to ${nextTheme === 'dark' ? 'dark' : 'light'} theme`}
            className="flex size-7 items-center justify-center rounded-sm border border-chrome-2 text-chrome-fg hover:bg-chrome-2"
          >
            <LineIcon name={resolvedTheme === 'dark' ? 'moon' : 'sun'} size={14} />
          </button>
          <Link
            to="/settings"
            title="Settings"
            aria-label="Settings"
            className="flex size-7 items-center justify-center rounded-sm border border-chrome-2 text-chrome-fg hover:bg-chrome-2"
          >
            <LineIcon name="settings" size={14} />
          </Link>
        </div>
      </header>

      {/* --------------------------- context bar ----------------------------- */}
      {/* The well switcher is pinned left because it is the one control a user
          reaches for on every screen; the readouts scroll beside it so nothing
          is ever pushed off the edge at 1024px. */}
      <div className="flex h-9 shrink-0 items-center gap-3 border-b border-line bg-surface-1 px-3 text-xs">
        <label className="flex shrink-0 items-center gap-1.5">
          <LineIcon name="well" size={13} className="text-accent" />
          <span className="sr-only">Current well</span>
          <select
            value={currentWellId}
            onChange={(event) => setCurrentWell(event.target.value)}
            aria-label="Current well"
            className="h-7 max-w-[200px] rounded-sm border border-line-strong bg-surface-1 px-2 text-xs font-semibold text-fg-strong"
          >
            {wells.length === 0 && <option value="">No wells loaded</option>}
            {wells.map((well) => (
              <option key={well.id} value={well.id}>
                {well.name} · {well.field}
              </option>
            ))}
          </select>
        </label>

        <div className="scroll-thin flex min-w-0 flex-1 items-center gap-4 overflow-x-auto">
          {currentWell ? (
            <>
              <Badge tone={wellStatusTone(currentWell.status)}>
                {humanizeEnum(currentWell.status)}
              </Badge>
              <ContextStat label="MD" value={fmtMeters(currentWell.current_depth_md)} />
              <ContextStat label="TVD" value={fmtMeters(currentWell.current_tvd)} />
              <span className="flex shrink-0 items-center gap-1.5">
                <span className="text-2xs uppercase tracking-[0.06em] text-fg-muted">
                  Formation
                </span>
                <span className="text-xs font-semibold text-fg-strong">
                  {currentWell.current_formation.name}
                </span>
                {formationIsProxy ? <ProxyTag>15/9 proxy</ProxyTag> : null}
              </span>
              <span className="flex shrink-0 items-center gap-1.5">
                <span className="text-2xs uppercase tracking-[0.06em] text-fg-muted">
                  Risk
                </span>
                <Badge tone={bandTone(currentWell.top_alert_severity)}>
                  {currentWell.top_alert_severity
                    ? humanizeEnum(currentWell.top_alert_severity)
                    : 'None open'}
                </Badge>
              </span>
              <ContextStat
                label="Offsets"
                value={`${nearby?.items.length ?? currentWell.offset_well_count} within ${fmtKm(radiusKm, 0)}`}
              />
            </>
          ) : (
            <span className="text-fg-muted">No well selected — records unavailable.</span>
          )}
        </div>
      </div>
      {/* ----------------------- data provenance banner --------------------- */}
      {/* Real public records, cited. The methodology lives in Settings. */}
      <div className="prov-strip flex shrink-0 items-center gap-2 px-3 py-1 text-2xs">
        <LineIcon name="database" size={12} className="shrink-0 text-warn" />
        <span className="font-semibold uppercase tracking-[0.08em] text-fg-strong">
          {meta?.dataset_label ?? 'Real public data'}
        </span>
        <span className="truncate text-fg-muted">
          {STATIC_DATA
            ? 'Read-only build — served from a published snapshot of the real API responses.'
            : `${meta?.data_sources?.length
                ? meta.data_sources.map((source) => source.publisher).filter(Boolean).join(' · ')
                : 'Norwegian Petroleum Directorate FactPages · Equinor Volve'}` +
              ' · scores are heuristics, not operational guidance.'}
        </span>
        <Link
          to="/settings"
          className="ml-auto shrink-0 font-semibold text-accent underline underline-offset-2"
        >
          Data sources
        </Link>
      </div>

      {/* ------------------------------- body -------------------------------- */}
      <div className="flex min-h-0 flex-1">
        <nav
          aria-label="Primary"
          className={cx(
            'scroll-thin flex shrink-0 flex-col border-r border-line bg-surface-1 py-2 transition-[width] duration-150',
            navCollapsed ? 'w-12' : 'w-56',
          )}
        >
          {NAV.map((group) => (
            <div key={group.heading} className={cx(navCollapsed ? 'mb-1' : 'mb-1.5')}>
              {navCollapsed ? (
                <div className="mx-2 my-1 border-t border-line" />
              ) : (
                <p className="px-3 py-1 text-2xs font-semibold uppercase tracking-[0.1em] text-fg-muted">
                  {group.heading}
                </p>
              )}
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  title={navCollapsed ? item.label : undefined}
                  className={({ isActive }) =>
                    cx(
                      'flex items-center gap-2 border-l-2 py-1.5 text-sm transition-colors',
                      navCollapsed ? 'justify-center px-0' : 'px-3',
                      isActive
                        ? 'border-accent bg-accent-soft font-semibold text-accent'
                        : 'border-transparent text-fg-muted hover:bg-surface-2 hover:text-fg-strong',
                    )
                  }
                >
                  <LineIcon name={item.icon} size={14} className="shrink-0" />
                  {navCollapsed ? (
                    <span className="sr-only">{item.label}</span>
                  ) : (
                    item.label
                  )}
                </NavLink>
              ))}
            </div>
          ))}

          <button
            type="button"
            onClick={toggleNav}
            aria-pressed={navCollapsed}
            title={navCollapsed ? 'Expand navigation' : 'Collapse navigation'}
            className={cx(
              'mt-auto flex items-center gap-2 border-t border-line pt-2 text-2xs font-semibold uppercase tracking-[0.06em] text-fg-muted transition-colors hover:text-fg-strong',
              navCollapsed ? 'justify-center px-0' : 'px-3',
            )}
            style={{ marginTop: 'auto' }}
          >
            <LineIcon
              name="chevronRight"
              size={13}
              className={cx('transition-transform', !navCollapsed && 'rotate-180')}
            />
            {navCollapsed ? null : 'Collapse'}
          </button>
        </nav>

        <main className="scroll-thin min-w-0 flex-1 overflow-y-auto">
          {error && (
            <div className="m-3 flex items-start gap-2 rounded-md border border-crit-line bg-crit-soft px-3 py-2">
              <LineIcon name="warning" size={14} className="mt-px shrink-0 text-crit" />
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold text-crit">API unavailable</p>
                <p className="text-2xs text-crit/90">{error}</p>
              </div>
              <button
                type="button"
                onClick={refresh}
                className="shrink-0 rounded-sm border border-crit px-2 py-1 text-2xs font-semibold uppercase text-crit"
              >
                Retry
              </button>
            </div>
          )}
          {children}
        </main>
      </div>

      {/* ---------------- derived-data note for the active well --------------- */}
      {/* 15/9-F-9A has no published formation rows and no published coordinate
          row. The context bar flags the proxy; this line states why once, for
          the whole app, instead of repeating it in every screen body. */}
      {(formationIsProxy || positionIsProxy) && currentWell ? (
        <div className="flex shrink-0 items-center gap-2 border-t border-line bg-surface-1 px-3 py-1 text-2xs text-fg-subtle">
          <ProxyTag>Derived</ProxyTag>
          <span className="truncate">
            {`${currentWell.name}: the formation column and position are derived from the published 15/9 block records — the Directorate publishes no rows for this wellbore. Methodology in Settings.`}
          </span>
        </div>
      ) : null}

      {/* ------------------------------ overlays ----------------------------- */}
      {evidenceEventId && <EvidenceDrawer eventId={evidenceEventId} onClose={closeEvidence} />}
      {decisionAlertId && <DecisionPanel alertId={decisionAlertId} onClose={closeDecision} />}
      <ToastViewport
        toast={toast ? { id: toast.id, message: toast.message, tone: toast.tone } : null}
        onDismiss={dismissToast}
      />
    </div>
  )
}

function ContextStat({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex shrink-0 items-baseline gap-1.5">
      <span className="text-2xs uppercase tracking-[0.06em] text-fg-muted">{label}</span>
      <span className="tnum text-xs font-semibold text-fg-strong">{value}</span>
    </span>
  )
}

const TOAST_TONE: Record<Tone, string> = {
  neutral: 'border-line-strong bg-surface-1 text-fg',
  info: 'border-accent-line bg-accent-soft text-accent',
  success: 'border-ok-line bg-ok-soft text-ok',
  warning: 'border-warn-line bg-warn-soft text-warn',
  critical: 'border-crit-line bg-crit-soft text-crit',
  muted: 'border-line bg-surface-2 text-fg-muted',
}

function ToastViewport({
  toast,
  onDismiss,
}: {
  toast: { id: number; message: string; tone: Tone } | null
  onDismiss: () => void
}) {
  if (!toast) return null
  return (
    <div className="pointer-events-none fixed bottom-3 left-1/2 z-[60] -translate-x-1/2">
      <div
        key={toast.id}
        role="status"
        className={cx(
          'pointer-events-auto flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs shadow-[var(--shadow-raised)]',
          TOAST_TONE[toast.tone],
        )}
      >
        <span>{toast.message}</span>
        <button type="button" onClick={onDismiss} aria-label="Dismiss notification" className="opacity-60 hover:opacity-100">
          <LineIcon name="close" size={11} />
        </button>
      </div>
    </div>
  )
}
