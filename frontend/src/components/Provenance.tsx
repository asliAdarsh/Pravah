import type { ReactNode } from 'react'
import type { ProvenanceText, TextProvenance } from '@/api/types'
import { Badge, cx, type Tone } from './primitives'

const PROVENANCE_TONE: Record<TextProvenance, Tone> = {
  RULE_BASED_TEMPLATE: 'info',
  MODEL_GENERATED: 'warning',
  SOURCE_DOCUMENT: 'success',
  SYSTEM_RULE: 'neutral',
}

const PROVENANCE_HINT: Record<TextProvenance, string> = {
  RULE_BASED_TEMPLATE: 'Assembled by the rule engine from stored records — not a language model.',
  MODEL_GENERATED: 'Produced by a configured language model. Verify against the linked records.',
  SOURCE_DOCUMENT: 'Quoted from the stored document excerpt.',
  SYSTEM_RULE: 'Stated by a configured rule of the risk engine.',
}

/** Mandatory label on every generated sentence (contract §0.4). */
export function ProvenanceTag({
  provenance,
  model,
  className,
}: {
  provenance: TextProvenance
  model?: string | null
  className?: string
}) {
  return (
    <span className={cx('inline-flex', className)} title={PROVENANCE_HINT[provenance]}>
      <Badge tone={PROVENANCE_TONE[provenance] ?? 'neutral'}>
        {provenance === 'MODEL_GENERATED' && model ? `MODEL · ${model}` : provenance}
      </Badge>
    </span>
  )
}

export function ProvenanceNote({ text }: { text: string }) {
  return <p className="text-2xs leading-snug text-fg-muted italic">{text}</p>
}

/** A generated sentence + its mandatory provenance label + source reference. */
export function GeneratedText({
  text,
  provenance,
  model,
  sourceEventId,
  sourceDocument,
  onOpenSource,
  className,
}: {
  text: string
  provenance: TextProvenance
  model?: string | null
  sourceEventId?: string | null
  sourceDocument?: string | null
  onOpenSource?: (eventId: string) => void
  className?: string
}) {
  return (
    <li className={cx('py-1.5', className)}>
      <p className="text-sm leading-snug text-fg">{text}</p>
      <div className="mt-1 flex flex-wrap items-center gap-1.5">
        <ProvenanceTag provenance={provenance} model={model} />
        {sourceDocument && <span className="text-2xs text-fg-muted">{sourceDocument}</span>}
        {sourceEventId &&
          (onOpenSource ? (
            <button
              type="button"
              onClick={() => onOpenSource(sourceEventId)}
              className="text-2xs font-semibold text-accent underline underline-offset-2 hover:text-accent"
            >
              [OPEN EVIDENCE]
            </button>
          ) : (
            <span className="text-2xs text-fg-muted">{sourceEventId}</span>
          ))}
      </div>
    </li>
  )
}

export function GeneratedList({
  items,
  onOpenSource,
  emptyLabel = 'No items returned by the engine.',
  className,
}: {
  items: ProvenanceText[]
  onOpenSource?: (eventId: string) => void
  emptyLabel?: string
  className?: string
}) {
  if (items.length === 0) {
    return <p className={cx('py-1 text-xs text-fg-muted', className)}>{emptyLabel}</p>
  }
  return (
    <ul className={cx('divide-y divide-line', className)}>
      {items.map((item, index) => (
        <GeneratedText
          key={`${item.source_event_id ?? 'src'}-${index}`}
          text={item.text}
          provenance={item.provenance}
          sourceEventId={item.source_event_id ?? null}
          sourceDocument={item.source_document ?? null}
          onOpenSource={onOpenSource}
        />
      ))}
    </ul>
  )
}

/** The disclaimer the API ships with every synthesis block. */
export function Disclaimer({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <p
      className={cx(
        'rounded-sm border border-warn-line bg-warn-soft px-2 py-1 text-2xs leading-snug text-warn',
        className,
      )}
    >
      {children}
    </p>
  )
}
