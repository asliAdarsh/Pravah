/**
 * Pravah UI kit — the single import surface for screens.
 *
 *   import { Card, DataTable, Badge, WhyFactors } from '@/components/ui'
 *
 * Hand-rolled industrial UI: sharp radii, dense 13px body text, tabular numerals,
 * status badges, restrained line icons. No component library, no chart library.
 *
 * Every colour is a semantic token (docs/UX_CONTRACT.md §1) so light and dark
 * both work; the raw ink/navy/brand palette is for chart fills only.
 */
export {
  Badge,
  Card,
  DataTable,
  EmptyState,
  KeyStat,
  Meter,
  Metric,
  Panel,
  SectionTitle,
  Skeleton,
  Toolbar,
  actionButtonClass,
  controlClass,
  cx,
  ghostButtonClass,
  type DataTableColumn,
  type DataTableProps,
  uniqueReasons,
  type Tone,
} from './primitives'

export {
  Callout,
  ControlSlider,
  Disclosure,
  KeyValueGrid,
  ScreenHeader,
  SectionCard,
  SegmentedControl,
  StatRow,
} from './kit'

export { WhyFactors } from './WhyFactors'
export { DepthAxis } from './DepthAxis'
export { EvidenceDrawer } from './EvidenceDrawer'
export { DecisionPanel } from './DecisionPanel'
export { Disclaimer, GeneratedList, GeneratedText, ProvenanceTag } from './Provenance'
export { LineIcon, type IconName } from './LineIcon'
