# Demo script — the narrated walkthrough, on real data

Two terminals: `python backend/run.py` and `cd frontend && npm run dev` → `http://localhost:5173`.
Landing state is the live well **15/9-F-9A**, Volve field, at **512.52 m MD / TVD**, oil-based,
16,670 real telemetry samples logged between 273.1 m and 1,206.0 m MD.

Everything below is a measured output, not a scripted line. See `docs/DATA_SOURCES.md` for the
corpus and the one derivation that is labelled as a proxy.

---

## 1 — The well is real, and we say which parts are derived

The context bar reads: `15/9-F-9A · Block 15/9 (Volve) · DRILLING · MD 513 m · TVD 513 m ·
FORMATION HORDALAND GP. Top · 15/9 PROXY · RISK CRITICAL · OFFSETS 3 within 8 km`.

The `15/9 PROXY` tag matters and should be pointed at. The Norwegian Petroleum Directorate
publishes no lithostratigraphy or coordinate row for this wellbore, so the app derives both
from the five real wells in the same 15/9 block and labels them. The footer says so in full:
*"the formation column and position are derived from the published 15/9 block records — the
Directorate publishes no rows for this wellbore."*

> **Say:** *"Every number here comes from a published file. Where the source has nothing, we say
> so rather than inventing it."*

## 2 — Nearby real wells, and why they are relevant

**Offset Intelligence → Offset Map.** Three real NPD wells inside the 8 km radius, plotted with
true North Sea coordinates:

| Well | Distance | Relevance | Band |
|---|---|---|---|
| 15/9-15 | 4.83 km | 0.74 | HIGH |
| 15/9-13 | 5.38 km | 0.70 | HIGH |
| 15/9-23 | 6.95 km | 0.68 | MEDIUM |

Click a well: the similarity breakdown, the factor list with `contribution = weight × value`,
and the depth range. **Relevance Method** explains where the weights come from — twelve per-hazard
AHP profiles, each a Saaty pairwise matrix reduced by eigenvector, with a consistency ratio against
Saaty's 0.10 threshold (stuck pipe: CR 0.045, mud programme weighted 18.3% because mud weight sets
the overbalance margin).

> **Say:** *"Weights are engineering priors, not fitted values. The matrix and the consistency
> ratio are both on screen, so you can argue with them."*

## 3 — What the live telemetry is doing

**Detection → Telemetry Monitor.** The current snapshot of 17 channels with units, depths measured
at, and sample counts; 11 of 17 are watched by a hazard rule. The plot is a measured stream by
depth, one lane per channel, each on its own scale.

Two honesty notes are deliberately prominent:

- *"Newest recorded readings, not live values — 16 of 17 channels have their newest measurement
  at a row other than the anchor sample. This is a recorded drilling run: a channel the stopped
  logging stops updating, so it is shown at its last measured value and marked stale rather than
  held flat."*
- Gaps are real gaps. The hookload channel has **no measurement above 300.1 m**, and the UI
  shows that band as unmeasured rather than extrapolating. The API's own note warns that a value
  missing from a plotted point may just be "not this bucket's extreme", and the plot uses the
  per-channel coverage block to tell the two apart.

## 4 — The alert, and the evidence behind it

**Risk → Alerts.** Four alerts, all from live telemetry, all with a `TELEMETRY` tag:

| Alert | Hazard | Channel | Interval | Alarms | Risk |
|---|---|---|---|---|---|
| AL-44FA12 | Overpressure | Total SPM | 502 m | 4 | 0.86 |
| AL-9FF47C | Mud loss | Pump 1 Stroke Rate | 502 m | 4 | 0.86 |
| AL-6013E1 | Stuck pipe | Average Hookload | 311 m | 12 | 0.85 |
| AL-A50C35 | Torque spike | CSOB | 311 m | 2 | 0.65 |

They read *"4 alarm observations · no per-wellbore DDR attribution"* rather than a
supporting-well count, because the published Volve DDR release is field-level and no offset well
can be given an attributable event. Open one: the evidence chain ends in
`TELEMETRY — Total SPM 1/min at 502 m MD — CUSUM, value 134.00 vs baseline 126.03 (threshold 1.18)`
then `WELL — 15/9-F-9A, live telemetry record`.

## 5 — How much warning, and how confident

**Detection → Backtest & Validation.** This is the screen that earns trust.

> **Headline:** lead distance **311.3 m** (from 307.7 m MD to the incident at 619.0 m MD);
> lead time **82.4 minutes** at the observed mean ROP of 226.7 m/h over 2,033 real ROP samples.
> 858 alerts before the incident, of 1,794 on the well.

Presented next to it, deliberately:

- *"Lead converted with the observed mean ROP… The median ROP of 52.2 m/h would give 358.1
  minutes; the two differ because the top of the hole is drilled fast."* — two conversions,
  neither presented as the truth.
- Episode precision: **Wilson 95% interval 23.5%–38.5%**, point estimate 30.5% from 43 of 141
  episodes, with the sentence *"This is an interval on one episode definition, not a measure of
  accuracy, and it is not comparable to a per-sample hit rate."*
- The leakage audit, collapsed by default: six causality checks, all passed, `✓ NO FUTURE
  LEAKAGE`.

The incident is named and sourced: `NO_2014-02-05_EVT_STUCK_PIPE`, 619.0 m MD, row 4,663, Equinor
Volve field record. Press **RUN BACKTEST** to recompute it in front of the judges.

> **Say:** *"The 311 metres is my number, from my detector, on a real recorded log, measured
> against a real incident. Here is the uncertainty on it, and here is the proof the detector
> never looked ahead."*

## 6 — Why the detector cannot cheat

Open **Leakage audit**. The suite behind it truncates the series at rows 1,500 / 3,000 / 4,663 /
9,000 and asserts the alerts for the shared prefix are byte-identical to the full run; that the
CUSUM recursion state matches; that the rolling window never contains a later sample; and that a
full run can never alert earlier than a truncated one. Gaps in the log **reset** the CUSUM state
rather than carrying a stale sum across an interval that was never measured.

## 7 — Historical knowledge, and the honest limit

**Knowledge → Knowledge Search.** *"stuck pipe"* returns 44 real records — events with their
well, depth and the DDR file they came from, each opening to the stored excerpt of the line the
event was read from.

**Knowledge → Graph Explorer.** The graph is 4,249 nodes and 13,404 edges over seven node types
(wells, formations, events, hazards, interventions, outcomes, report snippets). GraphRAG returns
snippets **with the hop path that reached them** — `TORQUE_SPIKE @ 256 m MD — 1 × Extracted From —
snippet EVX-EV-00018-091`.

State the limit plainly: the public DDR release is field-level, so the field's real history sits
with the live well, and the offset wells contribute real stratigraphy and casing but no
attributable events. Every document is titled *"Volve field Daily Drilling Report … — wellbore not
published in this release"*.

## 8 — Close it on the human

**Alerts → open one → ACKNOWLEDGE** with a note. The action is recorded, the status flips, and
the alert centre updates. Nothing else in the product can close an alert.

---

## Anticipated questions

| Question | Answer |
|---|---|
| Is the 311 m lead a validated result? | It is a measurement of **this** detector on this well's own recorded log against a named real incident, reported with a Wilson interval and a leakage audit. It is not a claim about the method in general. |
| Why 311 m and not more? | The first precursor lands at 307.7 m, near the top of the logged interval, so the lead is bounded by where the telemetry starts. We did not tune thresholds to improve it. |
| Is the risk score a probability? | No. The alert risk is a weighted density/statistic/distance composite, shown with its factors. The only interval on screen is the Wilson bound, and it is labelled as an interval on an episode definition. |
| Where does OCR fit? | The pipeline runs ocr → layout → sections → entities → events → depth normalisation → formation mapping. With no OCR engine installed the stage reports `SKIPPED` rather than pretending. The real data here needs no OCR: it arrives as text. |
| What does the LLM do? | Optional prose only, strictly downstream of retrieved records, with citations and provenance. With no key configured every sentence is a deterministic template and `model` stays `null`. It never touches a score, a filter or a rule. |
| Why SQLite and not PostGIS? | No server is guaranteed at the venue. Distance is one function (`app/geo.py`); the schema is PostgreSQL-portable. See `docs/ASSUMPTIONS.md`. |
| Why a custom SVG map? | No tile server at the venue. The well location plan renders offline, and the brief calls the map a navigation layer, not the product. |
