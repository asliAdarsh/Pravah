# Architecture

Pravah prototype — how the system is put together, and why.

```
┌────────────────────────────── React + TypeScript + Tailwind v4 ──────────────────────────────┐
│  AppShell (IA nav · role selector · always-visible current-well context bar · data banner)   │
│                                                                                              │
│  Dashboard │ OffsetMap │ OffsetReplay │ Timeline │ Alerts+DecisionPanel │ Search │ Documents │
│                                      ↕                                                          │
│                        EvidenceDrawer (openable from any screen)                             │
└──────────────────────────────────────┬───────────────────────────────────────────────────────┘
                                       │ JSON over /api/v1  (no scoring maths in the browser)
┌──────────────────────────────────────┴───────────────────────────────────────────────────────┐
│                                          FastAPI                                            │
│  routers/  validate + delegate                                                                │
│      │                                                                                       │
│      ├─ services.py    payload builders (nearby · timeline · replay · evidence · decision)     │
│      ├─ relevance.py   OFFSET RELEVANCE ENGINE  ── configurable weights, factor list          │
│      ├─ risk.py        PROACTIVE ALERT ENGINE   ── rules, risk breakdown, q1..q6, mitigations  │
│      ├─ search.py      HYBRID RETRIEVAL        ── intent parse + structured + lexical        │
│      ├─ ingestion.py   DOCUMENT PIPELINE       ── ocr→layout→sections→entities→events        │
│      ├─ llm.py         PROVIDER ABSTRACTION     ── optional; deterministic fallback           │
│      ├─ geo.py         haversine · trajectory · tvd/md                                        │
│      └─ event_types.py EVENT TYPE REGISTRY      ── one line to add a hazard type              │
│                                                                                              │
│  models.py → SQLAlchemy 2.0 → SQLite (PostgreSQL-portable)                                  │
│  seed.py   → deterministic synthetic dataset (SYNTHETIC_PROTOTYPE)                            │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Layering rules

1. **Routers never compute.** They validate path/query/body input, call a service or engine,
   and serialise. Any business maths lives in `relevance.py` / `risk.py` / `search.py`.
2. **Engines never touch HTTP.** `relevance.py` and `risk.py` take a session plus plain config
   and return dicts. That is why they are unit-testable without a server.
3. **The browser never computes relevance or risk.** The UI renders the `factors[]` the engine
   returned. One implementation of the maths means explainability cannot drift from the number
   shown on screen.
4. **Evidence is the source of truth.** Scores and prose are derived; the database record
   (event → document → evidence span) is what a claim ultimately rests on.

## Data model

```
Formation 1───* Well            (Well.current_formation_id, depth-band lookup by TVD range)
Well     1───* Document         (DDR / WCR / DGR / INCIDENT_REPORT / WELL_LOG / LESSONS_LEARNED)
Well     1───* DrillingEvent    (event_type, md, tvd, severity, description, mitigation)
Document 1───* Evidence         (page | null, section, text_span, extraction_method)
DrillingEvent *──* Alert         (via AlertEventLink, with per-event contribution)
Alert    1───* EngineerAction   (ACKNOWLEDGE / NOTE / STATUS_CHANGE / REVIEW)
Well     *───* Well              (via OffsetRelation cache — distance_km + 4 similarities + score)
AppConfig                        (relevance weights, risk thresholds — runtime editable)
```

`OffsetRelation` is a **cache of the relevance engine**, recomputed whenever weights, radius or
risk config change. It is never the authority: the engine can always recompute it from source
rows, so a stale cache cannot silently corrupt a ranking.

Depth → formation is a **range lookup** on `Formation.top_depth/bottom_depth`, not a per-well
hand-entered value, so a synthetic record cannot contradict the formation table.

## Offset-well relevance (explainable)

```
relevance = 0.40 · formation_similarity      (same formation band at the current TVD?)
         + 0.30 · depth_similarity           (ΔTVD between wells, decaying with distance)
         + 0.20 · spatial_proximity          (haversine distance inside the radius)
         + 0.10 · event_similarity           (shared historical hazard vocabulary)
```

Each signal produces a `factor` object `{code, label, value, weight, contribution, detail}`.
`contribution == weight · value` for every factor, which the test suite asserts — so the number
on screen is always reconstructable by the reader. Weights are editable at runtime via
`POST /config/relevance` (validated to sum ≈ 1), and the UI states "prototype defaults, not
scientifically validated" wherever a score appears.

Distance alone never drives the rank: spatial proximity is capped at 20% of the score.

## Proactive alert engine (explainable rules)

Fires when **all** hold (all thresholds runtime-configurable via `POST /config/risk`):

| Condition | Default |
|---|---|
| Event's formation is the same as, or adjacent to, the current formation | `formation_match_required = true` |
| Current TVD within tolerance of the historical event TVD | `tvd_tolerance_m = 60` |
| Supported by ≥ N relevant offset wells | `min_support_wells = 2` |
| Those wells pass the relevance floor | `min_relevance = 0.25` |

```
risk = 0.35·historical_event_match + 0.25·depth_proximity + 0.20·formation_similarity
     + 0.15·nearby_well_support     + 0.05·operational_similarity      (normalised to 0–1)

band: WARNING ≥ 0.45 · HIGH ≥ 0.60 · CRITICAL ≥ 0.78
```

`risk_factors[]` carries the same `code / value / weight / contribution / detail` shape as the
relevance factors, and `evidence_chain[]` walks Alert → Reason → Event → Well → Document →
Evidence so the audit trail is a rendered list, not a claim. These are prototype heuristic
scores — never described as probabilities, accuracies or validated risk.

## Knowledge retrieval

1. **Intent parse** (`search.py`): event-type vocabulary, formation names, "around N m" (TVD)
   vs "at N m MD", explicit radius, deictic phrasing ("around here", "nearby"), mitigation
   intent. Ambiguity is resolved by longest match and reported in `parsed_intent.explain`.
2. **Structured retrieval**: events near the anchor TVD within the radius of the current well,
   joined to wells, documents, mitigations, evidence and alerts.
3. **Lexical fallback**: deterministic TF-IDF-style scoring over event text and document text
   when the query carries no parseable intent. No network, reproducible ranking.
4. **Synthesis**: optional LLM prose, strictly downstream of the retrieved rows, always with
   `provenance` (`RULE_BASED_TEMPLATE` or `MODEL_GENERATED`), `model` and citations. With no key
   configured the text is assembled by template from the retrieved records and the model field
   stays `null`. The UI renders structured results first and the synthesis block second.

## Document ingestion

`OCR → layout → sections → entities → events → depth normalisation → formation mapping`

Each stage returns `{stage, status, detail, simulated}`, and the response shows them all. The
OCR backend is pluggable: PaddleOCR if importable, otherwise an explicit `SKIPPED` with
`simulated: true` stating that the embedded text layer was used. Section detection and entity
extraction are deterministic rules tuned to WCR/DDR conventions. Depth normalisation converts
recorded depths to metres TVD and assigns the formation by range lookup. Nothing is silently
skipped, and nothing claims extraction it did not perform.

## Appearance layer

Colour is resolved through **semantic tokens**, never the raw palette:

```
raw palette (index.css @theme)     navy / brand / ok / warn / crit / ink ramps
        │  kept for chart hue choices only
        ▼
semantic tokens (@theme inline)     surface · line · fg · chrome · accent · ok/warn/crit · grid · plot-bg
        │  light values in :root,[data-theme='light']
        │  dark  values in [data-theme='dark']
        ▼
components                         bg-surface-1 · border-line · text-fg-muted · bg-accent-soft …
```

`@theme inline` keeps the `var()` reference live, so a single attribute flip re-themes the
whole app — no second stylesheet, no per-component branch. `src/theme/useTheme.ts` owns the
preference (`light` / `dark` / `system`), persists it, writes `<html data-theme>`, and for
`system` subscribes to `matchMedia` so the app follows the OS live. `index.html` resolves the
same preference **before first paint**, so a dark-mode user never gets a white flash.

Components are forbidden from using the raw palette. The only permitted exceptions are SVG
plots, which read `var(--grid)`, `var(--accent-soft)`, `var(--warn)` and friends. Grepping
`src/pages` and `src/components` for `bg-ink-*` / `text-navy-*` returns zero — that is the cheap
regression guard for "did anyone reintroduce a hardcoded colour".

---

## Why these technologies

| Chosen | Reason | Rejected alternative |
|---|---|---|
| FastAPI | Typed, fast, self-documenting `/docs` for judges | Node/Express — the scoring and parsing work is numeric/text work |
| SQLAlchemy 2.0 + SQLite | Zero-dependency demo; schema portable to Postgres | Postgres/PostGIS — no server guaranteed at the venue |
| Python `haversine` | Microseconds at this scale, one function to swap | PostGIS `ST_Distance` — requires the server we cannot assume |
| Custom SVG map | Cannot fail without a tile server | MapLibre/Leaflet raster tiles — network dependency at the venue |
| Custom SVG charts | Exact control of the industrial look, zero bundle cost | Recharts/ECharts — heavier, generic look |
| Optional LLM | Prose only, never truth | Making the LLM the answer source — forbidden by the brief |
| In-process lexical index | Deterministic, offline | External embedding API — non-reproducible demo |

Every rejection is a demo-environment constraint, not a claim that the alternative is worse in
production. `docs/ASSUMPTIONS.md` records the production path for each.
