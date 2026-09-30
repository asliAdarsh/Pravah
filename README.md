# Pravah

**Enhanced Real-Time Monitoring, Analysis and Correlation with Near-Well Intelligence**

A drilling-intelligence prototype that answers the only question that matters on a rig floor:

> *At the current drilling depth, what happened previously in comparable nearby wells, why is
> it relevant, what evidence supports it, and what should the engineer consider?*

```
CURRENT WELL → CURRENT DEPTH / TVD → NEARBY OFFSET WELLS → DEPTH + FORMATION CORRELATION
   → HISTORICAL EVENT REPLAY → EVIDENCE → PROACTIVE ALERT → HISTORICAL MITIGATION
   → ENGINEER DECISION
```

> ## Data provenance — real published records
> Pravah runs on **real public data**, not invented rows:
>
> | Source | Publisher | Used for |
> |---|---|---|
> | Lithostratigraphy, member formations | Norwegian Petroleum Directorate (FactPages) | 119 real well coordinates, 85 real formation tops |
> | Lithostratigraphy groups, casing depths | Norwegian Petroleum Directorate (FactPages) | stratigraphic column, casing correlation |
> | Volve Daily Drilling Reports (≈30k) | Equinor Volve public DDR release | real 24-hour operations logs; 1,600+ events extracted from verbatim prose with stored evidence spans |
> | Volve high-frequency telemetry | Equinor / NPD open data | 16,670 real MWD/mud-logger samples for 15/9-F-9A, 273.1–1206.0 m MD |
>
> Nothing is fabricated: depths come from the DDR text, evidence spans are substrings of the
> report they cite, telemetry channels are the exported measurements, and gaps are shown as
> gaps rather than interpolated. Operator-supplied documents are labelled
> `OPERATOR_SUPPLIED_UNVERIFIED` and kept separate from the published corpus.
>
> What is **not** claimed: risk and relevance scores are prototype heuristics, not validated
> probabilities. The one measured performance figure — the telemetry lead time — is reported with
> its Wilson 95% interval and its precision definition on the Validation screen.

---

## Quick start (two terminals)

### 1. Backend — FastAPI + SQLite

```bash
pip install -r backend/requirements.txt
python backend/run.py
```

- API: `http://127.0.0.1:8000` · interactive docs: `http://127.0.0.1:8000/docs`
- The corpus is loaded automatically on first start: 119 real wells, 85 real formations,
  ~30k indexed DDR reports, 1,600+ real events with evidence spans, 16,670 real telemetry
  samples, plus the derived offset-relation cache and alert set.
- Re-seed from scratch: `PRAVAH_RESET_DB=1 python backend/run.py`, or delete
  `backend/data/pravah.db`.

### 2. Frontend — React + TypeScript + Tailwind

```bash
cd frontend
npm install
npm run dev
```

- App: `http://localhost:5173` (the dev server proxies `/api` to the backend).
- Production build: `npm run build`, then `npm run preview` — `preview` uses the same proxy,
  so a built bundle also works out of the box.

### Verify it is alive

```bash
curl http://127.0.0.1:8000/api/v1/health
curl "http://127.0.0.1:8000/api/v1/alerts?well_id=15%2F9-F-9A"
```

### Tests

```bash
python -m pytest backend/tests -q     # 285 passed
```

---

## The demo scenario (real data, deterministic)

The live well is **15/9-F-9A** in the Volve field, at **512.52 m MD / TVD**, oil-based, with
16,670 real MWD/mud-logger samples logged between 273.1 m and 1,206.0 m MD.

**1 — Nearby real wells.** Three real NPD wells fall inside the default 8 km radius:

| Offset well | Distance | Relevance | Basis |
|---|---|---|---|
| 15/9-15 | 4.83 km | 0.74 | same formation band at this TVD, nearby, comparable depth |
| 15/9-13 | 5.38 km | 0.70 | same formation band |
| 15/9-23 | 6.95 km | 0.68 | same formation band |

Relevance is AHP-weighted per hazard (formation, depth, spatial, shared history, and
mud/section where the hazard calls for it) — see `docs/ARCHITECTURE.md`.

**2 — What the live telemetry is doing.** The causal anomaly engine (z-score + CUSUM) reads the
real channels and raises four localised alerts, clustered by depth:

| Hazard | Channel | Interval | Alarms | Risk |
|---|---|---|---|---|
| Mud loss | Pump 1 Stroke Rate | 502 m | 4 | 0.86 |
| Overpressure | Total SPM | 502 m | 4 | 0.86 |
| Stuck pipe | Average Hookload | 311 m | 12 | 0.85 |
| Torque spike | CSOB | 311 m | 2 | 0.65 |

**3 — The validated backtest.** Run against the **confirmed** Volve stuck-pipe incident
`NO_2014-02-05_EVT_STUCK_PIPE` at **619.0 m MD**, the detector fires a first precursor at
**307.7 m MD** — a lead of **311.3 m**, or **82 minutes** at the observed mean ROP. The
backtest also reports the honest caveat that the mean ROP is inflated by the fast surface
interval (the median, 52.2 m/h, would give 358 minutes), and gives a **Wilson 95% interval of
0.235–0.385** on 43 precursor episodes out of 141 trials. The leakage audit reports all six
causality checks green.

**4 — Historical knowledge.** The Volve DDR corpus is published per *field*, not per wellbore,
so offset wells carry real stratigraphy and casing but no attributable events. The evidence
chain therefore runs through the field's real DDR text and the live telemetry, and the app
labels that scope rather than implying per-well evidence it does not have.

Follow `docs/DEMO_SCRIPT.md` for the narrated walkthrough.

---

## Screens

| # | Screen | Route | What it proves |
|---|---|---|---|
| 1 | Active Well Dashboard | `/` | Current well context, risk state, top offsets with why-relevant, open alerts |
| 2 | Offset Well Map | `/offset-intelligence/map` | Radius, filters, per-well similarity breakdown + factors |
| 3 | **Offset Well Replay** | `/offset-intelligence/replay` | Current position vs offset history on one shared TVD scale |
| 4 | Depth & Event Timeline | `/events/timeline` | Depth-aware lanes, formation transitions, ★ YOU ARE HERE |
| 5 | Risk / Alert Centre | `/alerts` | Rule, risk breakdown, why-this-alert factors, severity bands |
| 6 | Evidence / Source Viewer | drawer + `/documents` | Audit chain, stored excerpt, ingestion pipeline status |
| 7 | Knowledge Search | `/search` | Structured results first, labelled synthesis second |
| 8 | Engineer Decision Panel | `/alerts/:alertId` | q1–q6, evidence, acknowledge + engineer note (human-in-the-loop) |
| 9 | **Settings** | `/settings` | Appearance (light / dark / device), prototype role, relevance + risk engine controls, published data sources, how the prototype works |
| 10 | Telemetry Monitor | `/telemetry` | Live channel values with units and staleness, measured stream by depth — gaps drawn as gaps |
| 11 | Backtest & Validation | `/validation` | Lead distance/time against the named real incident, Wilson 95% interval, leakage audit |
| 12 | Relevance Method | `/relevance-method` | Per-hazard AHP weights, pairwise matrix, consistency ratio, engineering rationale |
| 13 | Knowledge Graph | `/graph` | Neighbourhood of a well/event node, and GraphRAG snippets with their hop path |

Every screen follows the same anatomy: a title, one filter bar, **one hero panel** that
answers the screen's question, at most one secondary row, and reference data collapsed behind
disclosures. The current-well context bar, the theme toggle and the data-provenance banner are
present on every screen.

## Appearance

Light, dark, or **Device** (follows the operating system, live — switch it at sunset and the
app follows without a reload). Set it in **Settings → Appearance**, with the toggle in the top
bar for one-click switching; the choice is remembered per browser. Dark mode is a genuine
second palette, not an inverted filter: every colour resolves through a semantic token, so
charts, badges and the evidence drawer all stay legible. A pre-paint script resolves the
stored preference before first paint, so there is no white flash on load.

Settings also absorbs everything that used to sit loose on the screens: the relevance weights,
the alert thresholds, the prototype role selector, the dataset counts and the method notes.
Nothing was lost — it moved to where it belongs.

---

## Configuration

All settings are environment variables — no secrets in code, none in the frontend.

| Variable | Default | Purpose |
|---|---|---|
| `PRAVAH_DATABASE_URL` | `sqlite:///backend/data/pravah.db` | Any SQLAlchemy URL; PostgreSQL is the production path |
| `PRAVAH_RESET_DB` | `0` | `1` reloads the real dataset on startup |
| `PRAVAH_MAX_DDR_REPORTS` | `4000` | how many real DDR reports to index (the published corpus holds ≈30k) |
| `PRAVAH_LLM_API_KEY` | unset | Enables optional LLM prose (`RULE_BASED_TEMPLATE` when absent) |
| `PRAVAH_LLM_BASE_URL` | OpenAI-compatible | LLM endpoint |
| `PRAVAH_LLM_MODEL` | — | Model id, echoed back so provenance is never faked |
| `PRAVAH_BACKEND_ORIGIN` | `http://127.0.0.1:8000` | Frontend dev/preview proxy target |

Relevance weights and alert thresholds are editable at runtime:
`GET/POST /api/v1/config/relevance` and `GET/POST /api/v1/config/risk`. Changing them
recomputes the offset relations and the alert set deterministically.

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) | Authoritative data model, every endpoint and payload, dataset requirements |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Layering, engines, relevance and alert formulas, technology choices |
| [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) | The real corpus, what is derived, and what is never fabricated |
| [`docs/ASSUMPTIONS.md`](docs/ASSUMPTIONS.md) | Every prototype simplification, with the production path for each |
| [`docs/UX_CONTRACT.md`](docs/UX_CONTRACT.md) | Screen anatomy, semantic colour tokens, kit API, dark-mode acceptance list |
| [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) | Twelve-step narrated walkthrough with expected on-screen values |
| [`backend/README.md`](backend/README.md) | Service layout, environment variables, test map |
| OpenAPI | `http://127.0.0.1:8000/docs` |

---

## Branching

Work lands on a feature branch, is aggregated on `features`, is integrated on `dev`, and
`main` is cut from `dev`.

```
main                      released, runs end to end
└── dev                   integration
    └── features          aggregate of the feature branches
        ├── feature/backend     service, engines, published datasets
        ├── feature/frontend    console, twelve screens, theming
        └── feature/docs        README and docs/
```

The feature branches are disjoint by area, so merging them into `features` is a union: no
conflict resolution and no code rewritten to land it. A change follows the same path —
commit on its feature branch, merge up through `features` and `dev`, then cut `main`.

```bash
git checkout feature/backend && git commit -am "…"
git checkout features && git merge --no-ff feature/backend
git checkout dev        && git merge --no-ff features
git checkout main       && git merge --no-ff dev
git push origin main dev features feature/backend feature/frontend feature/docs
```

### What is and is not committed

Committed: the application and the six published datasets under `backend/data_sources`
(~11 MB) — Pravah cannot seed without them, so a clone has to be able to run.

Ignored: `node_modules/`, `dist/`, the local SQLite database (`backend/data/`) and
`.env`. Nothing secret or machine-specific is in the history.

---

## Product principles enforced in code

1. **Evidence before AI** — scores and prose are derived; the event → document → evidence
   record is what a claim rests on, and every claim exposes it.
2. **Correlation before recommendation** — the engine ranks and alerts; recommendations are
   only historical mitigations found in source documents, or explicitly marked
   model-generated.
3. **Human-in-the-loop** — an alert is never auto-closed; an engineer acknowledges it and can
   attach a note. Both actions are recorded as `ENGINEER_ACTION` rows.
4. **Explain every alert** — every relevance and risk number ships with
   `factors[] = {code, label, value, weight, contribution, detail}` and
   `contribution == weight × value`, asserted by tests.
5. **Never fabricate source information** — unknown page numbers are `null` and render as
   "Page not available in this record"; excerpts are stored text; every extraction stage
   reports `SKIPPED` rather than pretending.
6. **The data is cited, not invented** — every record names the published file it came from
   (`data_provenance: REAL_PUBLIC_DATA`), the banner names the publishers, and the one value
   Pravah derives rather than reads (the live well's position and formation column) is tagged
   `15/9 PROXY` wherever it appears.
7. **Current-well context is always visible** — the context bar is fixed on every screen.
8. **Distance alone never ranks a well** — spatial proximity is capped at 20% of the relevance
   score; formation (40%) and depth (30%) dominate.
9. **The system decides nothing** — it presents correlated history, evidence and the
   historical response, then records what the engineer decided.
