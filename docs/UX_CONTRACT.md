# UX Redesign Contract — authoritative for this pass

This pass makes the prototype **calmer and easier to read**. The complaint it answers:
*"all the things is just everywhere and it is a little difficult to understand anything."*

Read this whole file before touching code. Three agents build in parallel against it; the
class-name mapping and the kit API below are exact. Do not invent replacements for them.

---

## 0. The three jobs of this pass

1. **Hierarchy.** Every screen must answer one question first, in one glance, with the
   supporting detail available but not shouting.
2. **Restraint.** Prose, method notes and reference data move out of the primary view into
   collapsible disclosures or into Settings. No wall of text on a working screen.
3. **Theme.** Light / dark / device, applied through semantic tokens only.

Non-negotiable product rules from `docs/API_CONTRACT.md` §0 still apply: synthetic data
labelled, evidence before AI, every score explainable, generated text labelled, red only for
critical.

---

## 1. Semantic colour tokens (already in `src/index.css` — do not edit that block)

A light and a dark value exist for each token. **Components must use these names, never the
raw `ink-*` / `navy-*` / `brand-*` / `ok-*` / `warn-*` / `crit-*` palette**, otherwise dark
mode breaks. The raw palette still exists in the same file for chart fills only (see §6).

| Purpose | Token → class | Replaces |
|---|---|---|
| App background | `bg-surface` | `bg-ink-100` |
| Card | `bg-surface-1`, `.panel-surface` | `bg-white` |
| Inset / table header / subtle block | `bg-surface-2`, `.inset-surface` | `bg-ink-50` |
| Hover / selected row | `bg-surface-3` | `bg-ink-100`/`bg-ink-50` on hover |
| Hairline border | `border-line` | `border-ink-200` |
| Stronger border / input | `border-line-strong` | `border-ink-300` |
| Primary text | `text-fg-strong` | `text-ink-900`/`text-ink-800` |
| Body text | `text-fg` | `text-ink-800`/`text-ink-700` |
| Muted text | `text-fg-muted` | `text-ink-600`/`text-ink-500` |
| Faint text, axes, captions | `text-fg-subtle` | `text-ink-400` |
| Accent (links, active nav, primary button) | `text-accent` / `bg-accent` / `bg-accent-soft` / `border-accent-line` | `brand-700`, `brand-50`, `brand-200` |
| Top bar / sidebar chrome | `bg-chrome`, `bg-chrome-2`, `text-chrome-fg`, `text-chrome-muted` | `navy-900`, `navy-800`, `white`, `navy-200` |
| Success | `text-ok`, `bg-ok-soft`, `border-ok-line` | `ok-600/700`, `ok-50`, `ok-200` |
| Warning | `text-warn`, `bg-warn-soft`, `border-warn-line` | `warn-600/700`, `warn-50`, `warn-200` |
| Critical | `text-crit`, `bg-crit-soft`, `border-crit-line` | `crit-600/700`, `crit-50`, `crit-200` |
| Chart gridline | `stroke-[var(--grid)]`, `text-[color:var(--grid)]` | `#e2e8f0`, `#f1f5f9` |
| Plot background | `.plot-surface` or `fill-[var(--plot-bg)]` | `#ffffff` |
| Severity dots | `bg-band-info/warning/high/critical` | unchanged, now themed |

Shadow tokens `--shadow-panel` / `--shadow-raised` are already applied by `.panel-surface`.

---

## 2. Screen anatomy — every screen, same skeleton

```
ScreenHeader        title · one-line purpose · dataset label · primary actions (right)
  └ optional Toolbar / FilterBar   one bar, all filters for the screen, sticky
      └ HERO panel                 the one thing the screen is for — full width
          └ secondary row          at most two panels side by side (6/4 or 7/5)
              └ Disclosure         reference data, method notes, full records
```

Rules:
- **One hero.** A screen with two competing "main" panels has failed.
- **Max two panels per row**, and only when they genuinely pair (map + detail, plot + controls).
- No control lives outside the filter bar of the panel it affects.
- Any block of prose (method notes, assumptions, "how this is computed") goes inside a
  `Disclosure` or into Settings — never loose in the page.
- Reference tables (full well record, rig parameters, document metadata) default **closed**.
- Every screen keeps the global current-well context bar and the synthetic-data banner. Do
  not duplicate them inside the page body.

---

## 3. Kit API — exact signatures

Extend `src/components/ui.tsx` (or add `src/components/kit.tsx` re-exported from `ui.tsx`).
Existing exports stay, re-skinned with the tokens in §1.

```ts
export function ScreenHeader({ title, subtitle, actions, meta }: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
  meta?: ReactNode            // small chips: counts, record ids, status
})

export function SectionCard({ title, description, actions, children, dense, className }: {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  dense?: boolean
  className?: string
})

export function StatRow({ children, className }: { children: ReactNode; className?: string })

export function KeyValueGrid({ items, columns, className }: {
  items: { label: string; value: ReactNode; tone?: Tone; mono?: boolean }[]
  columns?: 2 | 3 | 4
  className?: string
})

export function Disclosure({ summary, children, defaultOpen, tone, badge }: {
  summary: ReactNode
  children: ReactNode
  defaultOpen?: boolean
  tone?: Tone
  badge?: ReactNode
})

export function Callout({ tone, title, children, icon }: {
  tone: Tone
  title?: ReactNode
  children: ReactNode
  icon?: ReactNode
})

export function SegmentedControl<T extends string>({ value, options, onChange, label, size }: {
  value: T
  options: { id: T; label: ReactNode; hint?: string }[]
  onChange: (next: T) => void
  label: string
  size?: 'sm' | 'md'
})

export function KeyStat({ label, value, unit, hint, tone }: { /* existing Metric, re-skinned */ })
```

`Meter`, `Badge`, `DataTable`, `WhyFactors`, `DepthAxis`, `EvidenceDrawer`, `DecisionPanel`,
`ProvenanceTag`, `GeneratedText`, `GeneratedList`, `Disclaimer`, `LineIcon`, `EmptyState`,
`Skeleton`, `Toolbar`, `Panel`, `Card`, `cx`, `useAsyncData` all stay and keep their props.
Add the missing `LineIcon` names if a screen needs them (`sun`, `moon`, `monitor`,
`settings`, `chevron`, `sliders`, `info`, `check`, `x`, `external`).

`DepthAxis` must own a **default height** so no call site has to pass `className="h-[520px]"`
again.

---

## 4. Per-screen organisation (what changes)

### Dashboard `/`
1. **Hero — "Needs attention now"**: current well · TVD · formation, then the single highest
   open alert as a full-width alert card (hazard, band badge, interval, supporting wells, one
   line of why). `VIEW DECISION` + `REPLAY OFFSET WELLS` as the two actions.
2. **Second row**: `Depth vs TVD` curve (7 cols) | `Top offset wells` ranking table (5 cols).
3. **Third row, collapsed by default**: `Open alerts (n)` list | `Current well record`
   (rig, mud, ROP, WOB, bit, TD, spud date, coordinates).
4. The old "Jump to" card becomes a compact icon row in the header. Delete the free-standing
   method-note paragraphs.

### Offset Map `/offset-intelligence/map`
1. **Hero**: the location plan, full remaining width.
2. **Right rail (one column, scrolls)**: either the **selected well detail** or, when nothing is
   selected, the **ranking list**. Never both stacked.
3. Filters in a single bar above the map: radius slider, min relevance, formation, event type,
   well status, depth range. `FIT RADIUS` / `RESET` / `APPLY RADIUS TO ENGINE` move into an
   overflow menu, not three loose buttons.
4. The "relevance weights in use" panel moves to **Settings**; the map shows a single line
   linking to it.

### Offset Replay `/offset-intelligence/replay` *(signature screen — protect its clarity)*
1. **Hero**: the shared TVD scale, full width, tall. Current-well column, formation column, one
   lane per selected offset, amber `YOU ARE HERE` rule. Keep it visually dominant.
2. **Right rail**: depth window controls · event-type chips · selected wells · recurring
   hazards · alert strip.
3. The wide metric row collapses into a compact 3-value summary inside the plot header
   (Current TVD · Formation · Hole size). Mud weight / ROP move into a `Disclosure`.

### Depth Timeline `/events/timeline`
1. **Hero**: the depth lanes plot.
2. Filter bar: window ±, event type, well.
3. Second row: `Current vs offsets` comparison table, **collapsed by default**.
4. Formation transitions render as a slim band, not a full lane.

### Alerts `/alerts`, `/alerts/:id`
1. **Hero**: filter bar + alert list, grouped by severity band with band headers and counts.
2. The rule explanation becomes a `Disclosure` ("Rule, thresholds and how risk is computed")
   with a link to **Settings → Risk engine**.
3. Decision panel opens as-is (q1–q6, factors, actions).

### Knowledge Search `/search`
1. **Hero**: query bar + `How your query was interpreted`.
2. **Primary result**: the events table. Secondary groups (`Wells`, `Documents`,
   `Historical mitigation`, `Evidence`) become collapsed `Disclosure`s with counts in the
   summary, so the first screen is not a wall.
3. `Generated summary` moves to the bottom in a `Disclosure` labelled with its provenance and
   the disclaimer — structured records stay primary.

### Evidence & Documents `/documents`, `/documents/:id`
1. Two panes: register table | selected document detail.
2. Document metadata (type, system, pages, origin, hash) into a `Disclosure`; the excerpt,
   sections and linked events stay visible.
3. `Operator document ingest` becomes a collapsible panel (or modal) opened from a header
   button — not a permanent form above the register. Show the pipeline stage results inline.

---

## 5. Settings page `/settings` (new)

Single scrolling page, sections in this order. It absorbs what was scattered around the app.

1. **Appearance** — theme `SegmentedControl`: **Light / Dark / Device**, with the current
   resolved value shown ("Currently: Dark") and one line per option as the hint.
2. **Prototype role** — the role selector moves here from the top bar, with a note that it is
   a view switch, not authentication. Top bar keeps only: dataset badge, theme toggle,
   Settings link.
3. **Relevance engine** — the four weights as labelled sliders showing live percentages,
   radius, min relevance, `Apply` (posts `/config/relevance` and recomputes) and `Reset to
   defaults`. Show the resulting band thresholds. State plainly: prototype defaults, not
   scientifically validated.
4. **Risk engine** — min support wells, TVD tolerance, min relevance, formation-match required,
   severity band thresholds, `Apply` + `Reset`.
5. **Dataset & provenance** — counts (wells, formations, events, documents, evidence, alerts,
   offset relations), engine version, relevance/risk config as returned, LLM mode
   (`RULE_BASED_TEMPLATE` / model null), and `Reset demo data` (posts `/risk/recompute`;
   label it clearly as prototype data).
6. **How this prototype works** — the method text that used to sit loose on screens: how
   relevance is weighted, how the alert rule fires, what is simulated (OCR stage, synthetic
   evidence, page numbers), and what the LLM may and may not do.

Route `/settings`, nav item under a new **System** group in the sidebar. Settings is reachable
from the top-bar gear icon and from the sidebar.

---

## 6. SVG / chart colour rules

Charts currently hardcode hex fills (`#eff6ff`, `#1d4ed8`, `#94a3b8`, `#e2e8f0`, `#1c3f65`,
`#e07b16`, `#bfdbfe`, `#ffffff`). In dark mode these must invert or vanish. Convert them:

| Was | Becomes |
|---|---|
| gridline stroke `#e2e8f0` / `#f1f5f9` | `stroke-[var(--grid)]` (two weights via opacity) |
| axis label `#94a3b8` | `fill-[var(--fg-subtle)]` |
| curve stroke `#1c3f65` | `stroke-[var(--chrome-2)]` |
| area fill `#dbeafe` | `fill-[var(--accent-soft)]` |
| marker / warning stroke `#e07b16` | `stroke-[var(--warn)]` |
| marker fill `#e07b16` | `fill-[var(--warn)]` |
| band fill `#eff6ff` + stroke `#bfdbfe` | `fill-[var(--accent-soft)]` + `stroke-[var(--accent-line)]` |
| plot background `#ffffff` | `.plot-surface` or `fill-[var(--plot-bg)]` |
| map water/land/grid greys | `var(--surface-2)` / `var(--line)` / `var(--grid)` |

Keep the raw palette out of components entirely.

---

## 7. Dark-mode acceptance

A screen is done when, at 1680×1050 and at 1024×900, in **light and dark**:

- no hardcoded white/black backgrounds,
- body text ≥ 4.5:1 contrast on its surface, secondary text ≥ 3:1,
- borders visible without shouting,
- chart axes/labels legible, plots not glowing,
- severity colours still readable (critical stays the only red),
- the synthetic-data banner still distinguishable,
- focus rings visible.

---

## 8. Verification each agent must run

- `npm --prefix frontend run build` — zero TypeScript errors.
- In the browser against the live API (`python backend/run.py` is running on
  `http://127.0.0.1:8000`; `npm --prefix frontend run dev` on 5173):
  toggle the theme in Settings, visit every screen you own, and check the acceptance list
  in §7. Screenshot both themes.
- Report the commands you ran and what you actually observed. Never claim a check you did not
  run.

## 9. File ownership

| Agent | Owns |
|---|---|
| `KitShell` | `frontend/src/components/**`, `frontend/src/lib/**`, `frontend/src/store/**`, `frontend/src/App.tsx`, `frontend/src/main.tsx`, `frontend/src/theme/**`, `frontend/src/pages/Settings.tsx` |
| `CoreScreens` | `frontend/src/pages/Dashboard.tsx`, `OffsetMap.tsx`, `OffsetReplay.tsx`, `_shared.tsx` |
| `OpsScreens` | `frontend/src/pages/Timeline.tsx`, `Alerts.tsx`, `Search.tsx`, `Documents.tsx` |

Nobody edits `docs/**`, `backend/**`, `index.css` §1 tokens, or another agent's files.
`src/pages/_shared.tsx` belongs to `CoreScreens`; `OpsScreens` imports from it read-only and
must report any helper it needs that is missing.
