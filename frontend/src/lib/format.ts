import type { EventSeverity, SeverityBand, WellStatus } from '@/api/types'
import type { Tone } from '@/components/primitives'

/** Depth / TVD readouts. Tabular, unit-suffixed, no decimals. */
export function fmtMeters(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })} m`
}

export function fmtMetersShort(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${Math.round(value).toLocaleString('en-US')}m`
}

export function fmtNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function fmtPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

export function fmtSigned(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const sign = value > 0 ? '+' : value < 0 ? '−' : '±'
  return `${sign}${Math.abs(value).toFixed(digits)}`
}

export function fmtKm(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value.toFixed(digits)} km`
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return `${date.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })} ${date.toLocaleTimeString(
    'en-GB',
    { hour: '2-digit', minute: '2-digit' },
  )}`
}

/**
 * Contract §0.2 — the API may legitimately return `page: null`. The UI must never
 * substitute a number; it renders this string instead.
 */
export const PAGE_UNAVAILABLE = 'Page not available in this record'

/**
 * Human label for a score method. The API returns an internal enum; the enum's
 * own wording is an internal detail and is never rendered.
 */
export function scoreMethodLabel(method?: string | null): string {
  if (!method) return 'HEURISTIC'
  if (method === 'PROTOTYPE_HEURISTIC') return 'HEURISTIC'
  return method.replace(/_/g, ' ').toUpperCase()
}

export function fmtPage(page: number | null | undefined): string {
  if (page === null || page === undefined) return PAGE_UNAVAILABLE
  return `p. ${page}`
}

export function severityTone(severity: EventSeverity | null | undefined): Tone {
  switch (severity) {
    case 'CRITICAL':
      return 'critical'
    case 'HIGH':
      return 'critical'
    case 'MODERATE':
      return 'warning'
    case 'LOW':
      return 'success'
    default:
      return 'muted'
  }
}

export function bandTone(band: SeverityBand | null | undefined): Tone {
  switch (band) {
    case 'CRITICAL':
      return 'critical'
    case 'HIGH':
      return 'critical'
    case 'WARNING':
      return 'warning'
    case 'INFO':
      return 'info'
    default:
      return 'muted'
  }
}

export function wellStatusTone(status: WellStatus | null | undefined): Tone {
  switch (status) {
    case 'DRILLING':
      return 'info'
    case 'SUSPENDED':
      return 'warning'
    case 'COMPLETED':
      return 'success'
    case 'ABANDONED':
      return 'critical'
    default:
      return 'muted'
  }
}

/**
 * The relevance bands the engine itself applies (backend `RELEVANCE_BANDS`),
 * highest first, with the colour the UI paints them in. Keeping the cut-offs
 * here means a bar and the `HIGH / MEDIUM / LOW / MINIMAL` label the API
 * returns always agree.
 *
 * Deliberately no red in this ramp: a high relevance score is not a severity,
 * and red stays reserved for critical (bandDotClass / eventSeverityDotClass).
 */
export const RELEVANCE_BANDS: {
  code: string
  label: string
  min: number
  tone: Tone
  fill: string
}[] = [
  { code: 'HIGH', label: 'High', min: 0.7, tone: 'warning', fill: 'bg-band-high' },
  { code: 'MEDIUM', label: 'Medium', min: 0.45, tone: 'warning', fill: 'bg-band-warning' },
  { code: 'LOW', label: 'Low', min: 0.25, tone: 'info', fill: 'bg-band-info' },
  { code: 'MINIMAL', label: 'Minimal', min: 0, tone: 'muted', fill: 'bg-fg-subtle' },
]

/** Relevance score → visual band for bars/markers. The score itself comes from the API. */
export function relevanceTone(score: number | null | undefined): Tone {
  if (score === null || score === undefined) return 'muted'
  return RELEVANCE_BANDS.find((band) => score >= band.min)?.tone ?? 'muted'
}

/** Static class strings (Tailwind v4 cannot resolve interpolated names). */
export function relevanceFillClass(score: number | null | undefined): string {
  if (score === null || score === undefined) return 'bg-line-strong'
  return RELEVANCE_BANDS.find((band) => score >= band.min)?.fill ?? 'bg-fg-subtle'
}

export function bandDotClass(band: SeverityBand | null | undefined): string {
  switch (band) {
    case 'CRITICAL':
      return 'bg-band-critical'
    case 'HIGH':
      return 'bg-band-high'
    case 'WARNING':
      return 'bg-band-warning'
    case 'INFO':
      return 'bg-band-info'
    default:
      return 'bg-fg-subtle'
  }
}

export function eventSeverityDotClass(severity: EventSeverity | null | undefined): string {
  switch (severity) {
    case 'CRITICAL':
      return 'bg-band-critical'
    case 'HIGH':
      return 'bg-band-high'
    case 'MODERATE':
      return 'bg-band-warning'
    case 'LOW':
      return 'bg-ok'
    default:
      return 'bg-fg-subtle'
  }
}

/** Compact enum → human label ("STUCK_PIPE" → "Stuck Pipe"). */
export function humanizeEnum(value: string | null | undefined): string {
  if (!value) return '—'
  return value
    .toLowerCase()
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

export function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}
