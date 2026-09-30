# Assumptions & Prototype Simplifications

Every entry below is a deliberate, documented choice made to satisfy the product
requirements with the smallest implementation that works in a hackathon demo. None of them
changes the product loop. Where the brief suggested a technology and we did not use it, the
reason is stated.

## Data

| # | Assumption | Reason |
|---|---|---|
| A1 | All wells, events, documents and evidence spans are **synthetic and fictional** (`data_provenance: SYNTHETIC_PROTOTYPE`). | The brief forbids presenting synthetic data as real operator data. Every surface is labelled. |
| A2 | No integration with any operator system (no OIL India connector, no live rig telemetry). | Forbidden by the brief's failure conditions. |
| A3 | Evidence `text_span` excerpts are generated deterministically from templates at seed time and then **stored** in the database. The UI shows the stored string verbatim. | The rule is "never fabricate source information". We do not invent page numbers or claim OCR provenance: the `extraction_method` field says `RULE_TEMPLATE_SIMULATED`, and documents with no real page number return `page: null`. |
| A4 | Document page numbers are generated as part of the synthetic record (e.g. DDR p. 43) and stored, not inferred at render time. | A synthetic record is internally consistent; the alternative (always-null pages) would make the evidence panel untestable. `extraction_method` and `ocr_engine` always disclose how the record was produced. |

## Storage

| # | Assumption | Reason |
|---|---|---|
| A5 | **SQLite** (SQLAlchemy 2.0, file `backend/data/pravah.db`) instead of PostgreSQL + PostGIS for the running prototype; the schema is written to be PostgreSQL-portable (no SQLite-only types, no SQLite-only functions). | No database server may exist in the demo environment. Swapping `PRAVAH_DATABASE_URL` to Postgres is the intended production path. |
| A6 | Spatial distance uses a Haversine implementation in Python (`app/geo.py`) rather than PostGIS `ST_Distance`. At <50 wells this is microseconds and keeps the demo dependency-free. | Same reason as A5. The relevance engine consumes a `distance_km` scalar, so swapping in PostGIS changes one function. |
| A7 | No TimescaleDB hypertables. Depth/trajectory data is stored as sampled rows; the trajectory curve is rendered from a sampled series. | No Timescale extension available; the depth series in a drilling prototype is sparse (daily reports, trajectory surveys), so a hypertable buys nothing for this dataset. |
| A8 | Semantic retrieval uses a deterministic lexical index (TF-IDF-style vectors + cosine similarity computed in-process, with a curated event-type/mitigation vocabulary), not an external embedding service. | Avoids a network dependency and non-reproducible rankings in a live demo. The interface is a single `semantic_score()` function so a real embedding backend can replace it. |

## Engines

| # | Assumption | Reason |
|---|---|---|
| A9 | Offset-well relevance uses four normalised signals — formation similarity 0.40, depth/TVD similarity 0.30, spatial proximity 0.20, event similarity 0.10 — configurable at runtime via `POST /config/relevance`. | The brief's proposed weights. They are **prototype defaults, not validated**. Every response carries `method: PROTOTYPE_HEURISTIC` and the UI labels them as such. |
| A10 | The alert rule is: same (or adjacent) formation at current TVD AND current TVD within `tvd_tolerance_m` (default 60 m) of the historical event TVD AND support from ≥ `min_support_wells` (default 2) relevant offset wells AND alert-generating wells pass `min_relevance`. | The brief's rule. Thresholds are configurable via `POST /config/risk`. |
| A11 | Risk score is a weighted sum of five explainable factors (`historical_event_match`, `depth_proximity`, `formation_similarity`, `nearby_well_support`, `operational_similarity`) normalised to 0–1, with every contribution returned in `risk_factors[]`. | The brief requires explainability, not a trained model. No accuracy or probability claim is ever made. |
| A12 | Severity bands (WARNING ≥ 0.45, HIGH ≥ 0.60, CRITICAL ≥ 0.78) are prototype thresholds, not risk probabilities. | Explicitly forbidden to imply scientific validation. |
| A13 | Alerts are deterministic and reconcilable: recomputation updates open alerts in place instead of creating duplicates, so a demo re-run is stable. | Repeatable demos. |

## LLM

| # | Assumption | Reason |
|---|---|---|
| A14 | The LLM is **optional**. `PRAVAH_LLM_API_KEY` + `PRAVAH_LLM_BASE_URL` + `PRAVAH_LLM_MODEL` enable an OpenAI-compatible chat call for summary prose only. With no key configured, every generated sentence comes from a deterministic template with `provenance: RULE_BASED_TEMPLATE` and `model: null`. | The brief forbids pretending. The LLM is never the source of truth and must never affect scores, filters or alert rules. |
| A15 | Retrieved structured results are always shown as the primary answer; LLM/templated text is secondary, labelled, and carries citations to the retrieved records. | Brief: "the conversational interface is secondary to the structured result". |

## Documents / OCR

| # | Assumption | Reason |
|---|---|---|
| A16 | The ingestion pipeline implements the full stage chain (OCR → layout → sections → entities → events → depth normalisation → formation mapping) with **pluggable backends**. The OCR stage is skipped with an explicit `simulated: true` note when no OCR engine is installed; PaddleOCR is used when importable. Layout analysis is rule-based (heading regex + section splitting), not LayoutLM. | "Use OCR where practical" — a heavyweight model in a demo environment is a liability. Every stage reports its status honestly so no stage overclaims. |
| A17 | PDF text extraction uses `pypdf` when available; scanned PDFs without a text layer fall through to the OCR backend or return a clear warning rather than empty results with no explanation. | Honest degradation. |
| A18 | Entity/event extraction from documents uses deterministic regex + vocabulary rules tuned to WCR/DDR conventions (depth ranges, event keyword phrases, mitigation clauses). | Deterministic, inspectable, testable; a trained extractor would be opaque and unverifiable in a demo. |

## Frontend

| # | Assumption | Reason |
|---|---|---|
| A19 | The Offset Well Map is a custom SVG "well location plan" (equirectangular projection, km radius rings, drag/wheel zoom, relevance-coloured well markers) instead of MapLibre/Leaflet raster tiles. | Tile servers may be unreachable at the venue; an offline-safe well plot cannot fail to render. The brief calls the map a navigation layer, not the product. |
| A20 | Charts (depth vs TVD curve, risk breakdown, factor bars, timeline) are hand-built SVG rather than Recharts/ECharts. | Fewer dependencies, exact control over the industrial look, no bundle bloat. |
| A18 | Entity/event extraction works **per event line**, not per section: one DDR day routinely records several hazards, so the depth attached to an event is the reading written on that event's own line, with a section-level reading only as a fallback for prose that names the hazard without repeating a number. A `MITIGATION:` / `REMEDIAL ACTION:` line is treated as a continuation field of the event above it (merged, then split off as the response), and also resolves from a following "Mitigation of Record" section. | An earlier section-granular version attributed 1,490 m (a progress line's depth) to a mud loss logged at 1,497 m, and produced one event per section. Depth correctness is the whole product; an off-by-seven-metre correlation is a wrong correlation. |
| A18b | Document extraction matches a **narrower vocabulary** than search. The registry keeps two term sets: `vocabulary` (phrases that indicate the hazard in source text) drives extraction, while `keywords` (short query/UI terms such as `rop`, `wob`, `bit`) drive search intent only. Code-form tokens (`MUD_LOSS`) extract as well, because they are unambiguous. | A DDR progress line — *"drilled ahead … at 14 m/h with 18 klb WOB"* — otherwise becomes a fabricated DRILLING_DYSFUNCTION event. Recall for extraction is not worth inventing hazards. |
| A22 | Authentication is a prototype role selector (Drilling Engineer / Rig-site / Office Engineer / Geoscientist) that changes which actions are offered. It is **not** authentication. | The brief states prototype-level role selection is sufficient. |
| A23 | Scoring maths lives only in the backend. The UI renders the factors returned by the API. | Two implementations of relevance maths would drift and break explainability. |

## Scope boundaries

- Trajectories are sampled survey points, not full directional drilling modelling.
- Real-time monitoring (MODBUS/WITS drilling data) is out of scope; "current well" state is a
  snapshot at the current depth. Any such label in the UI is stated as a snapshot.
- No production, reserve or drilling-performance claims are computed or displayed.
