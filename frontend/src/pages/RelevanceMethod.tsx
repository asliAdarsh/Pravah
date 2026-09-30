import { useEffect, useState } from 'react'
import {
  Badge,
  Callout,
  Disclosure,
  KeyStat,
  ScreenHeader,
  SectionCard,
  StatRow,
  controlClass,
  cx,
} from '@/components/ui'
import { ConsistencyMeter, WeightBars } from '@/components/visuals/WeightBars'
import { useAsyncData } from '@/lib/useAsyncData'
import { useApp } from '@/store/useApp'
import { fmtNumber, humanizeEnum } from '@/lib/format'
import { LABEL, ResourceState } from './_shared'
import { AhpApi } from './_engines'

/**
 * Screen 10 — Relevance method (AHP weights)
 *
 * The hero is the weight vector for one hazard and its consistency ratio. The
 * pairwise matrix that produced it is secondary, and the engineering rationale
 * and references sit behind a disclosure.
 *
 * The screen says plainly, in the open, that these judgements are engineering
 * priors. Nobody fitted them to incident data, and a consistency ratio only
 * proves the matrix is internally coherent — not that the ordering is right.
 */

/** Compact row/column header for the matrix: `Formation similarity` → `Form`. */
function shortFeature(feature: string): string {
  const [head, ...rest] = feature.split('_')
  return rest.length > 0 ? `${head.slice(0, 4)}. ${rest[0].slice(0, 3)}.` : head.slice(0, 6)
}

export function RelevanceMethod() {
  const { meta, notify } = useApp()
  const [hazard, setHazard] = useState('stuck_pipe')

  const index = useAsyncData((signal) => AhpApi.profiles(signal), [])
  const profile = useAsyncData((signal) => AhpApi.profile(hazard, signal), [hazard])

  const profiles = index.data?.profiles ?? []
  /* Land on the current well's own hazard watch once the list arrives. */
  useEffect(() => {
    if (profiles.length === 0 || profiles.some((entry) => entry.hazard === hazard)) return
    setHazard(profiles[0].hazard)
  }, [profiles, hazard])

  const data = profile.data
  const ranked = data
    ? data.features
        .map((feature) => ({ feature, weight: data.weights[feature] ?? 0 }))
        .sort((a, b) => b.weight - a.weight)
    : []
  const top = ranked[0]
  const weakest = ranked[ranked.length - 1]
  const threshold = data?.cr_threshold ?? index.data?.cr_threshold ?? 0.1

  return (
    <div className="flex flex-col gap-4">
      <ScreenHeader
        title="Relevance method"
        subtitle="How offset relevance is weighted for each hazard, and how coherent those judgements are. The pairwise comparisons are engineering priors, not values fitted to incident data."
        meta={
          <>
            <Badge tone="warning">{meta?.dataset_label ?? 'Real public data'}</Badge>
            {index.data ? (
              <Badge tone="info">{index.data.count} hazard profiles</Badge>
            ) : null}
            {data ? (
              <Badge tone={data.consistent ? 'success' : 'warning'}>
                CR {fmtNumber(data.consistency_ratio, 4)}
              </Badge>
            ) : null}
          </>
        }
      />

      {/* One filter: which hazard's weights are on screen. */}
      <div className="flex flex-wrap items-end gap-3">
        <label className="min-w-[220px]">
          <span className={LABEL}>Hazard</span>
          <select
            value={hazard}
            onChange={(event) => setHazard(event.target.value)}
            className={cx(controlClass, 'mt-1 w-full text-fg-strong')}
          >
            {profiles.length === 0 ? <option value={hazard}>Loading…</option> : null}
            {profiles.map((entry) => (
              <option key={entry.hazard} value={entry.hazard}>
                {entry.label} · CR {fmtNumber(entry.consistency_ratio, 3)}
              </option>
            ))}
          </select>
        </label>
        <p className="pb-1 text-2xs text-fg-muted">
          {index.data
            ? `${index.data.method.replace(/_/g, ' ').toLowerCase()}, Saaty threshold ${index.data.cr_threshold}.`
            : ''}{' '}
          {index.data?.general_hazard
            ? `“${humanizeEnum(index.data.general_hazard)}” is the fallback when a hazard has no profile of its own.`
            : ''}
        </p>
      </div>

      <ResourceState
        loading={profile.loading}
        error={profile.error}
        onRetry={profile.reload}
        skeleton={<div className="h-72 skeleton-block" />}
      >
        {data ? (
          <>
            {/* ----------------------------- hero ------------------------------ */}
            <SectionCard
              title={`${data.label} — feature weights`}
              description="Principal eigenvector of the pairwise comparison matrix, normalised to sum to 1."
            >
              <StatRow>
                <KeyStat
                  label="Leading criterion"
                  value={top ? top.feature.replace(/_/g, ' ') : '—'}
                  hint={top ? `${fmtNumber(top.weight * 100, 1)}% of the total weight` : undefined}
                  tone="info"
                />
                <KeyStat
                  label="Weakest criterion"
                  value={weakest ? weakest.feature.replace(/_/g, ' ') : '—'}
                  hint={
                    weakest
                      ? `${fmtNumber(weakest.weight * 100, 1)}% — ${top && weakest ? `${fmtNumber(top.weight / Math.max(weakest.weight, 1e-9), 1)}× less than the leader` : ''}`
                      : undefined
                  }
                />
                <KeyStat
                  label="Criteria"
                  value={String(data.features.length)}
                  hint={`${data.matrix.length}×${data.matrix.length} comparison matrix`}
                />
                <KeyStat
                  label="Consistency ratio"
                  value={fmtNumber(data.consistency_ratio, 4)}
                  tone={data.consistent ? 'success' : 'warning'}
                  hint={`against Saaty ${data.cr_threshold}`}
                />
              </StatRow>

              <div className="mt-3 grid gap-4 lg:grid-cols-5">
                <div className="lg:col-span-3">
                  <WeightBars features={data.features} weights={data.weights} />
                </div>
                <div className="lg:col-span-2">
                  <ConsistencyMeter
                    ratio={data.consistency_ratio}
                    threshold={threshold}
                    lambdaMax={data.lambda_max}
                    criteriaCount={data.features.length}
                    method={data.method}
                  />
                </div>
              </div>

              <div className="mt-3">
                <Callout tone="warning" title="These weights are engineering priors">
                  Every entry in the comparison matrix is a judgement made from drilling practice
                  and published guidance, not a value estimated from this dataset. A consistency
                  ratio of {fmtNumber(data.consistency_ratio, 4)} says the judgements agree with
                  each other; it says nothing about whether they are correct. Changing a cell
                  changes the weights, and the screen would still show a consistent ratio.
                </Callout>
              </div>
            </SectionCard>

            {/* -------------------------- secondary --------------------------- */}
            <div className="mt-4">
              <SectionCard
                title="Pairwise comparison matrix"
                description="Row criterion against column criterion, on Saaty's 1–9 scale. Reciprocal cells are the transpose; the diagonal is 1."
                dense
              >
                <div className="scroll-thin overflow-x-auto">
                  <table className="border-collapse text-sm">
                    <thead>
                      <tr>
                        <th className="sticky left-0 bg-surface-1 px-2 py-1.5" />
                        {data.features.map((feature) => (
                          <th
                            key={feature}
                            scope="col"
                            className="px-2 py-1.5 text-right text-2xs font-semibold uppercase tracking-[0.05em] text-fg-muted"
                          >
                            {shortFeature(feature)}
                          </th>
                        ))}
                        <th
                          scope="col"
                          className="px-2 py-1.5 text-right text-2xs font-semibold uppercase tracking-[0.05em] text-fg-muted"
                        >
                          Weight
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.features.map((rowFeature, rowIndex) => (
                        <tr key={rowFeature} className="border-t border-line">
                          <th
                            scope="row"
                            className="sticky left-0 bg-surface-1 px-2 py-1 text-left text-2xs font-medium text-fg-muted"
                          >
                            {rowFeature.replace(/_/g, ' ')}
                          </th>
                          {data.features.map((colFeature, colIndex) => {
                            const value = data.matrix[rowIndex]?.[colIndex]
                            const diagonal = rowIndex === colIndex
                            return (
                              <td
                                key={colFeature}
                                className={cx(
                                  'tnum px-2 py-1 text-right',
                                  diagonal ? 'text-fg-subtle' : 'text-fg',
                                )}
                              >
                                {value === undefined
                                  ? '—'
                                  : fmtNumber(value, value % 1 === 0 ? 0 : 2)}
                              </td>
                            )
                          })}
                          <td className="tnum px-2 py-1 text-right font-semibold text-fg-strong">
                            {fmtNumber((data.weights[rowFeature] ?? 0) * 100, 1)}%
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-2 text-2xs leading-relaxed text-fg-muted">
                  A value above 1 means the row criterion is judged more important than the column
                  criterion; the reciprocal cell carries the same judgement the other way round.
                  λ<sub>max</sub> {fmtNumber(data.lambda_max, 3)} over{' '}
                  {data.features.length} criteria gives the consistency ratio above.
                </p>
              </SectionCard>
            </div>

            {/* --------------------------- collapsed --------------------------- */}
            <div className="mt-4 flex flex-col gap-2">
              <Disclosure
                summary="Engineering rationale"
                badge={<Badge tone="muted">prior, not fitted</Badge>}
              >
                <p className="text-xs leading-relaxed text-fg-muted">
                  {data.engineering_rationale}
                </p>
              </Disclosure>

              <Disclosure summary={`References (${data.references.length})`}>
                <ul className="space-y-1">
                  {data.references.map((reference) => (
                    <li
                      key={reference}
                      className="text-2xs leading-relaxed text-fg-muted before:mr-1.5 before:text-fg-subtle before:content-['—']"
                    >
                      {reference}
                    </li>
                  ))}
                </ul>
              </Disclosure>

              <Disclosure
                summary={`All ${profiles.length} hazard profiles`}
                badge={<Badge tone="neutral">comparison</Badge>}
              >
                <ul className="space-y-1">
                  {profiles.map((entry) => (
                    <li key={entry.hazard}>
                      <button
                        type="button"
                        onClick={() => {
                          setHazard(entry.hazard)
                          notify(`Showing the ${entry.label} relevance profile`, 'info')
                        }}
                        className={cx(
                          'flex w-full items-baseline justify-between gap-2 rounded-sm px-2 py-1 text-left text-2xs hover:bg-surface-2',
                          entry.hazard === hazard ? 'bg-accent-soft' : '',
                        )}
                      >
                        <span className="text-fg">{entry.label}</span>
                        <span className="tnum shrink-0 text-fg-muted">
                          {entry.features.length} criteria · CR{' '}
                          {fmtNumber(entry.consistency_ratio, 3)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </Disclosure>
            </div>
          </>
        ) : null}
      </ResourceState>
    </div>
  )
}
