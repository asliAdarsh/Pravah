# Pravah — backend

FastAPI service for the Pravah drilling-intelligence prototype.

**All data is synthetic.** Every payload that carries dataset data is labelled
`"data_provenance": "SYNTHETIC_PROTOTYPE"` (operator uploads are labelled
`OPERATOR_SUPPLIED_UNVERIFIED`), and every relevance/risk score is labelled
`"method": "PROTOTYPE_HEURISTIC"` — these are heuristics, not validated
predictions.

## Quick start

```bash
pip install -r backend/requirements.txt
python backend/run.py            # http://127.0.0.1:8000  (docs at /docs)
```

The database is created and seeded automatically on first start. To force a
regeneration, set `PRAVAH_RESET_DB=1`.

Run the tests from the repository root:

```bash
python -m pytest backend/tests -q
```

## Configuration

Every setting is an environment variable with the `PRAVAH_` prefix. A `.env` file
next to `run.py` is also read. No secret is stored in code.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PRAVAH_DATABASE_URL` | `sqlite:///backend/data/pravah.db` | SQLAlchemy URL. Any server database works — the schema is PostgreSQL/PostGIS portable. |
| `PRAVAH_RESET_DB` | `false` | Drop and regenerate the dataset on startup. |
| `PRAVAH_LLM_API_KEY` | *(unset)* | OpenAI-compatible API key. **Unset means deterministic template synthesis.** |
| `PRAVAH_LLM_BASE_URL` | `https://api.openai.com/v1` | Chat-completions base URL. |
| `PRAVAH_LLM_MODEL` | `gpt-4o-mini` | Model id; only echoed when a call actually succeeds. |
| `PRAVAH_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated browser origins. |
| `PRAVAH_SEED` | `20240517` | Seed for the deterministic dataset generator. |
| `PRAVAH_HOST` / `PRAVAH_PORT` | `127.0.0.1` / `8000` | Bind address for `run.py`. |
| `PRAVAH_RELOAD` | `0` | Uvicorn auto-reload. |

## Module map

| Module | Responsibility |
| --- | --- |
| `app/config.py` | Env-driven `Settings` (pydantic-settings). |
| `app/db.py` | Engine/session factory, SQLite FK + WAL pragmas, `get_db` dependency. |
| `app/event_types.py` | **Single registry** of the 12 event types: label, family, `severity_weight`, colour, search vocabulary. Adding a type is a one-line change; the risk engine, search parser and `GET /meta` all read from it. |
| `app/models.py` | SQLAlchemy 2.0 declarative entities. |
| `app/geo.py` | Haversine, bearing, trajectory sampling, TVD/MD interpolation. |
| `app/seed.py` | Deterministic generator (`random.Random(20240517)`). |
| `app/relevance.py` | Four-signal offset scoring with a `factors[]` explainability list. |
| `app/risk.py` | Rule engine, alert reconciliation, and the `q1..q6` decision panel. |
| `app/llm.py` | Provider abstraction with a non-negotiable deterministic fallback. |
| `app/search.py` | NL intent parsing, structured retrieval, TF-IDF lexical fallback, template answers. |
| `app/ingestion.py` | `ocr → layout → sections → entities → events → depth_normalization → formation_mapping`. |
| `app/schemas.py` | Pydantic request models and the ORM → dict serialisers. |
| `app/services.py` | Composite read models assembled from the engines. |
| `app/errors.py` | Domain errors → `{"detail": ...}` responses. |
| `app/routers/` | Validate-and-delegate HTTP layer only. |

## The engines

### Relevance (`app/relevance.py`)

```
relevance = w_f·formation_similarity + w_d·depth_similarity
          + w_s·spatial_proximity   + w_e·event_similarity
```

Defaults `0.4 / 0.3 / 0.2 / 0.1`. Weights are normalised defensively
(`normalize_weights`), so a malformed client payload can never push the score
outside `[0, 1]`. Every component emits a `factors[]` record —
`{code, label, value, weight, contribution, detail}` — so the UI renders
"WHY THIS" from data and never re-derives the arithmetic.

`refresh_relations()` writes the `offset_relations` cache for every well pair, so
any read is a table lookup rather than an O(n²) recompute.

### Risk (`app/risk.py`)

For a current TVD and each event type, the engine collects offset events inside
`± tvd_tolerance_m` (then widens the interval once so a straddling cluster is
captured whole) and fires when
`supporting_well_count ≥ min_support_wells` and, if required, the formation
relationship is `SAME` or `ADJACENT`.

```
risk = w_h·historical_event_match + w_p·depth_proximity + w_f·formation_similarity
     + w_n·nearby_well_support   + w_o·operational_similarity
```

Defaults `0.35 / 0.25 / 0.20 / 0.15 / 0.05`, mapped to
`INFO < WARNING(0.45) < HIGH(0.60) < CRITICAL(0.78)`.

Alert ids are a SHA-1 digest of `(current_well_id, event_type, interval_key)`,
with the interval floored to a 5 m grid. **A recompute therefore updates the
existing row and can never duplicate an alert.** Alerts that stop firing are
deactivated (`is_active = false`), never deleted, so the audit trail survives a
rule change.

## The synthetic dataset

Generated from `random.Random(20240517)`; no wall-clock time enters a stored
record except explicit spud/completion dates.

* 18 wells; `DEMO-01` active, `DRILLING` at **1,500 m MD / 1,500 m TVD** in
  `Barail`.
* 7 formations stacking monotonically from 0 m; depth → formation is always a
  **range lookup** (`formation_at_tvd`), never a hand-written per-well value.
* 78+ events across all 12 registered types, ≥ 400 m apart within a well.
* 90+ documents (DDR per event day, WCR per completed well, 2 lessons-learned,
  1 incident report); every event has ≥ 1 evidence row.
* Anchored cluster, all within 6 km of `DEMO-01` and all drilled through
  `Barail`:

  | Well | Event | TVD | Severity |
  | --- | --- | --- | --- |
  | `DEMO-02` | `MUD_LOSS` | 1,485 m | HIGH |
  | `DEMO-03` | `STUCK_PIPE` | 1,490 m | HIGH |
  | `DEMO-04` | `STUCK_PIPE` | 1,520 m | CRITICAL |
  | `DEMO-05` | `STUCK_PIPE` | 1,498 m | HIGH |
  | `DEMO-06` | `MUD_LOSS` | 1,502 m | MODERATE |
  | `DEMO-07` | `STUCK_PIPE` | 1,510 m | CRITICAL |

### Honest metadata

`evidence.text_span` **is** the stored excerpt — a citation can never point at
text the backend does not hold. `page` is `None` for roughly 1 in 4 rows (daily
summaries, lessons-learned entries) and the API reports `null`, so the UI can
render "Page not available in prototype record". A page is never invented.

## LLM behaviour

`PRAVAH_LLM_API_KEY` unset → `synthesis.provenance = "RULE_BASED_TEMPLATE"` and
`model = null`, always. A provider error is caught and downgraded to the same
template. `MODEL_GENERATED` is emitted **only** when a call actually succeeded,
and the model name is the provider's own echo. The LLM may replace the answer
*text*; it never changes the structured results or the citations.

## Ingestion

`POST /api/v1/documents/ingest` accepts JSON (`text`) or multipart (`file`).

The OCR stage is pluggable. `PaddleOcrBackend` activates only when `paddleocr`
is importable; otherwise the stage reports `status: "SKIPPED"`,
`simulated: true`, and a `detail` string stating the embedded text layer was
used. `pypdf` is imported lazily inside a guard, so the app runs without it.

Unparseable input never raises: the response is `201` with `warnings` and
`extracted_events: []`. A document with no resolvable well is stored unattached
(`well_id: null`) with a warning, and its events are not persisted because an
event must belong to a well.

## Notes on the environment

SQLAlchemy 2.0.36 raises `TypeError` when it evaluates a `Mapped[X | None]`
annotation on Python 3.14, so optional columns in `app/models.py` are declared
with their non-optional type plus an explicit `nullable=True`. Runtime
nullability is unchanged.

## Prototype simplifications

* Distance is computed in Python (`geo.haversine_km`), not in PostGIS.
* Severity → band thresholds, `min_support_wells` and `tvd_tolerance_m` are
  judgement calls chosen to make the demo legible. They are **not** calibrated
  against real drilling data.
* The relevance weights are defaults, not a fit.
