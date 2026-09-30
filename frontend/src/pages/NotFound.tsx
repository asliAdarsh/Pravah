import { Link } from 'react-router-dom'
import { LineIcon } from '@/components/ui'
import { useApp } from '@/store/useApp'
import { SyntheticStrip } from './_shared'

/**
 * Screen 8 — 404. Deliberately minimal: one explanation, one way back, and
 * the prototype provenance line that every other surface carries.
 */
export function NotFound() {
  const { meta } = useApp()

  return (
    <div className="flex min-h-[60vh] items-center justify-center px-2">
      <div className="panel-surface w-full max-w-lg px-6 py-7 text-center">
        <p className="tnum text-3xl font-semibold tracking-tight text-fg-subtle">404</p>
        <h1 className="mt-1 text-base font-semibold text-fg-strong">Route not found</h1>
        <p className="mt-2 text-xs leading-relaxed text-fg-muted">
          This address does not match any screen in Pravah. The dashboard is the
          entry point; offset intelligence, the replay, the timeline, the alert centre and knowledge
          search are all reachable from there.
        </p>
        <div className="mt-4 flex items-center justify-center gap-2">
          <Link to="/" className="actionButtonClass">
            <LineIcon name="arrowRight" size={12} />
            Back to dashboard
          </Link>
        </div>
        <div className="mt-5 flex items-center justify-center">
          <SyntheticStrip label={meta?.dataset_label} />
        </div>
      </div>
    </div>
  )
}

export default NotFound
