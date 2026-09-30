import type { ReactNode } from 'react'

export type IconName =
  | 'alert'
  | 'database'
  | 'offset'
  | 'map'
  | 'replay'
  | 'timeline'
  | 'search'
  | 'document'
  | 'evidence'
  | 'well'
  | 'close'
  | 'x'
  | 'chevron'
  | 'chevronUp'
  | 'chevronRight'
  | 'check'
  | 'note'
  | 'arrowRight'
  | 'info'
  | 'warning'
  | 'layers'
  | 'filter'
  | 'open'
  | 'external'
  | 'refresh'
  | 'compass'
  | 'sun'
  | 'moon'
  | 'monitor'
  | 'settings'
  | 'sliders'

/** 24×24 stroke paths — restrained single-weight line icons, no fills. */
const PATHS: Record<IconName, ReactNode> = {
  alert: (
    <>
      <path d="M12 3.5 21 19.5H3z" />
      <path d="M12 9.5v4" />
      <path d="M12 16.6h.01" />
    </>
  ),
  database: (
    <>
      <ellipse cx="12" cy="6.5" rx="7" ry="2.6" />
      <path d="M5 6.5v11c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6v-11" />
      <path d="M5 12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6" />
    </>
  ),
  offset: (
    <>
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="3" />
      <path d="M12 1.5v3M12 19.5v3M1.5 12h3M19.5 12h3" />
    </>
  ),
  map: (
    <>
      <path d="M3 6.5 9 4l6 2.5L21 4v13.5L15 20l-6-2.5L3 20z" />
      <path d="M9 4v13.5M15 6.5V20" />
    </>
  ),
  replay: (
    <>
      <path d="M20 12a8 8 0 1 1-2.6-5.9" />
      <path d="M20 3.5V9h-5.5" />
    </>
  ),
  timeline: (
    <>
      <path d="M3 12h18" />
      <path d="M7 7.5v9M12 5v14M17 8.5v7" />
      <circle cx="7" cy="12" r="1.4" />
      <circle cx="12" cy="12" r="1.4" />
      <circle cx="17" cy="12" r="1.4" />
    </>
  ),
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m15.5 15.5 5 5" />
    </>
  ),
  document: (
    <>
      <path d="M14 3H7a1.5 1.5 0 0 0-1.5 1.5v15A1.5 1.5 0 0 0 7 21h10a1.5 1.5 0 0 0 1.5-1.5V7.5z" />
      <path d="M14 3v4.5h4.5M9 12h6M9 15.5h6" />
    </>
  ),
  evidence: (
    <>
      <path d="M5 4.5h14v15H5z" />
      <path d="M8.5 9h7M8.5 12.5h7M8.5 16h4" />
    </>
  ),
  well: (
    <>
      <path d="M12 2v20" />
      <path d="M7 5h10" />
      <path d="M5 9.5h14" />
      <path d="M3 14h18" />
    </>
  ),
  close: (
    <>
      <path d="m6 6 12 12M18 6 6 18" />
    </>
  ),
  x: (
    <>
      <path d="m6 6 12 12M18 6 6 18" />
    </>
  ),
  chevron: (
    <>
      <path d="m9 5 7 7-7 7" />
    </>
  ),
  chevronUp: (
    <>
      <path d="m5 15 7-7 7 7" />
    </>
  ),
  chevronRight: (
    <>
      <path d="m9 5 7 7-7 7" />
    </>
  ),
  check: (
    <>
      <path d="m4.5 12.5 5 5 10-11" />
    </>
  ),
  note: (
    <>
      <path d="M5 4.5h14v15H5z" />
      <path d="M8.5 9h7M8.5 12.5h7M8.5 16h4" />
    </>
  ),
  arrowRight: (
    <>
      <path d="M4 12h16" />
      <path d="m14 6 6 6-6 6" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6M12 7.5h.01" />
    </>
  ),
  warning: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.5v5.5M12 16.4h.01" />
    </>
  ),
  layers: (
    <>
      <path d="m12 3 9 4.5-9 4.5-9-4.5z" />
      <path d="m3 12 9 4.5 9-4.5" />
      <path d="m3 16.5 9 4.5 9-4.5" />
    </>
  ),
  filter: (
    <>
      <path d="M3 5h18l-7 8v6l-4 2v-8z" />
    </>
  ),
  open: (
    <>
      <path d="M14 4h6v6" />
      <path d="M20 4 11 13" />
      <path d="M18 14v5.5H4.5V6H10" />
    </>
  ),
  external: (
    <>
      <path d="M14 4h6v6" />
      <path d="M20 4 11 13" />
      <path d="M18 14v5.5H4.5V6H10" />
    </>
  ),
  refresh: (
    <>
      <path d="M20.5 12a8.5 8.5 0 1 1-2.5-6" />
      <path d="M20.5 3.5V9H15" />
    </>
  ),
  compass: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m15.5 8.5-2 5-5 2 2-5z" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.2 5.2l1.4 1.4M17.4 17.4l1.4 1.4M18.8 5.2l-1.4 1.4M6.6 17.4l-1.4 1.4" />
    </>
  ),
  moon: (
    <>
      <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5" />
    </>
  ),
  monitor: (
    <>
      <path d="M3.5 5h17v10h-17z" />
      <path d="M9 19h6M12 15v4" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.2 5.2l2.1 2.1M16.7 16.7l2.1 2.1M18.8 5.2l-2.1 2.1M7.3 16.7l-2.1 2.1" />
    </>
  ),
  sliders: (
    <>
      <path d="M3 7h10M17 7h4M3 17h4M11 17h10" />
      <circle cx="15" cy="7" r="2" />
      <circle cx="9" cy="17" r="2" />
    </>
  ),
}

export function LineIcon({
  name,
  size = 16,
  className,
  title,
}: {
  name: IconName
  size?: number
  className?: string
  title?: string
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden={title ? undefined : true}
      role={title ? 'img' : undefined}
      focusable="false"
    >
      {title && <title>{title}</title>}
      {PATHS[name]}
    </svg>
  )
}
