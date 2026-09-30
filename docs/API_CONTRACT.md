# Pravah — API Contract & Architecture (authoritative)

Both backend and frontend agents build against THIS file. Do not invent endpoints or
field names outside it; if something is genuinely missing, add it here first and keep
both sides consistent.

> **The JSON bodies below are illustrative payload shapes, not the live dataset.**
> They were written when Pravah ran on a synthetic corpus, so the identifiers
> (`DEMO-01`, `Barail`, `DEMO-05`) and depths appear throughout as examples. The running
> system now loads **real public data** — Norwegian Petroleum Directorate lithostratigraphy
> and casing, the Equinor Volve Daily Drilling Report corpus, and Volve high-frequency
> telemetry — so live values are real well ids like `15/9-F-9A`, real NPD horizons, and real
> depths. See `docs/DATA_SOURCES.md` for the corpus and the derivation rules.
>
> Two shape changes came with the corpus, both additive and both now live:
> `data_provenance` is `REAL_PUBLIC_DATA` (not `SYNTHETIC_PROTOTYPE`), and `GET /meta`
> carries `data_sources[]`. Well ids contain a slash, so per-well routes use the
> `{well_id:path}` converter.

Repo layout (fixed):

```
Prototype/
  backend/
    app/
      main.py          FastAPI app factory + CORS + routers + startup seed
      config.py        env-driven Settings (no secrets in code)
      db.py            SQLAlchemy engine/session, SQLite by default
      models.py        ORM entities
      schemas.py       pydantic response/request models
      seed.py          deterministic synthetic dataset generator
      relevance.py     offset-well relevance scoring (explainable)
      risk.py          proactive alert engine (explainable)
      search.py        hybrid structured + semantic retrieval + NL query parsing
      llm.py           LLM provider abstraction + deterministic fallback
      ingestion.py     document ingestion pipeline (OCR/extraction, pluggable)
      geo.py           haversine / trajectory helpers
      errors.py
      routers/         wells.py formations.py events.py alerts.py evidence.py
                       documents.py search.py config.py meta.py
    tests/
    requirements.txt
    run.py
  frontend/
    src/...
  docs/
```

---

## 0. Non-negotiable product rules

1. **All data is synthetic.** Every API payload that carries dataset data MUST include a
   label such as `"data_provenance": "SYNTHETIC_PROTOTYPE"`, and `GET /meta` exposes
   `dataset_label: "Prototype / Synthetic Data"`. No fake OIL India integration, no fake
   accuracy percentages, no fake citations.
2. **Never fabricate source metadata.** If a document has no real page number, `page` is
   `null` and the UI renders "Page not available in prototype record". Evidence
   `text_span` must be the actual generated excerpt text stored in the DB.
3. **Explainability is data, not prose.** Relevance and risk responses include
   `factors[]` with `{code, label, value, weight, contribution, detail}` so the UI can
   render "WHY THIS" without re-deriving math.
4. **LLM is optional and never the source of truth.** If no API key is configured, the
   backend returns deterministic template synthesis with
   `synthesis.mode = "RULE_BASED_FALLBACK"` and `synthesis.model = null`. Never emit text
   that pretends to be from a model.
5. Scores are labelled prototype heuristics everywhere (`"method": "PROTOTYPE_HEURISTIC"`).

---

## 1. Conventions

- Base URL in dev: `http://127.0.0.1:8000`
- JSON, snake_case fields. All endpoints under `/api/v1` prefix.
- Errors: FastAPI default `{"detail": ...}`; 404 for unknown ids.
- All list endpoints accept `limit`/`offset` where sensible; default limit 200.
- Timestamps ISO-8601 strings with timezone.

---

## 2. Enums

`WellStatus`: `PLANNED | DRILLING | SUSPENDED | COMPLETED | ABANDONED`
`DocumentType`: `DDR | WCR | DGR | INCIDENT_REPORT | WELL_LOG | LESSONS_LEARNED`
`EventType`: `MUD_LOSS | STUCK_PIPE | KICK | TORQUE_SPIKE | OVERPRESSURE |
LOST_CIRCULATION | CEMENTING_ISSUE | DRILLING_DYSFUNCTION | NPT | FORMATION_TRANSITION |
HOLE_INSTABILITY | OTHER`
`EventSeverity`: `LOW | MODERATE | HIGH | CRITICAL`
`AlertStatus`: `OPEN | ACKNOWLEDGED | DISMISSED | CLOSED`
`SeverityBand`: `INFO | WARNING | HIGH | CRITICAL`
`ActionType`: `ACKNOWLEDGE | NOTE | STATUS_CHANGE | REVIEW`

Adding an `EventType` must be a one-line change (a registry dict in
`app/event_types.py`) — the UI badge/colour map, risk engine, search parser and filters
all read from it. Expose it at `GET /meta` → `event_types`.

---

## 3. Endpoints

### GET `/api/v1/health`
`{status:"ok", version, engine_version, database, synthetic_data: true}`

### GET `/api/v1/meta`
```json
{
  "app": "Pravah",
  "version": "0.1.0-prototype",
  "dataset_label": "Prototype / Synthetic Data",
  "data_provenance": "SYNTHETIC_PROTOTYPE",
  "engine_version": "rule-engine-0.1.0",
  "counts": {"wells": 18, "formations": 6, "events": 72, "documents": 46, "alerts": 9},
  "event_types": [{"code":"STUCK_PIPE","label":"Stuck Pipe","family":"MECHANICAL","severity_weight":1.0,"color":"#C2410C"}],
  "severity_bands": [...],
  "relevance_config": { ...weights object... },
  "risk_config": { ...rule config... },
  "llm": {"configured": false, "mode": "RULE_BASED_FALLBACK"},
  "demo": {"current_well_id": "DEMO-01", "scenario": "..."}
}
```

### GET `/api/v1/formations`
`{items:[{id,name,code,top_depth,bottom_depth,lithology,depositional_environment,age,description,is_simulated}]}`

### GET `/api/v1/wells`
Query: `status, field, q, limit, offset`
```json
{"items":[WellSummary], "total": 18}
```
`WellSummary`: `{id,name,field,block,latitude,longitude,status,current_depth_md,current_tvd,
current_formation:{id,name},operator,well_type,spud_date,water_depth_m,is_active,
offset_well_count, relevant_event_count, top_alert_severity}`

### GET `/api/v1/wells/{well_id}`
Full `Well`:
```json
{
 "id","name","field","block","operator","well_type","status",
 "latitude","longitude","spud_date","completion_date","rig","mud_system","water_depth_m",
 "total_depth_md","current_depth_md","current_tvd",
 "current_formation":{"id","name","top_depth","bottom_depth","lithology"},
 "trajectory":[{"md":0,"tvd":0,"inclination":0}],"   // sampled, sorted by md
 "operating_context":{"section_size","bit_size","mud_weight_ppg","rop_mph","wob_klb","block":"8.5 in","status_note"},
 "offset_well_count":7,"relevant_event_count":11,"top_alert_severity":"HIGH",
 "data_provenance":"SYNTHETIC_PROTOTYPE"
}
```

### GET `/api/v1/wells/{well_id}/nearby`
Query: `radius_km (default 8)`, `min_relevance (0..1, default 0.15)`, `event_type`,
`formation`, `status`, `depth_min_md`, `depth_max_md`, `weights` handled globally via
`POST /api/v1/config/relevance`.
```json
{
 "current_well": WellSummary,
 "radius_km": 8.0,
 "weights": {"formation_similarity":0.4,"depth_similarity":0.3,"spatial_proximity":0.2,"event_similarity":0.1},
 "method": "PROTOTYPE_HEURISTIC",
 "items": [ OffsetWell ]
}
```
`OffsetWell`:
```json
{
 "well": WellSummary,
 "distance_km": 1.82,
 "relevance_score": 0.86,
 "relevance_band": "HIGH",
 "similarity": {"formation_similarity":1.0,"depth_similarity":0.83,"spatial_proximity":0.88,"event_similarity":1.0},
 "factors": [{"code":"FORMATION_MATCH","label":"Same formation at current TVD","value":1.0,"weight":0.4,"contribution":0.4,"detail":"Barail (current) vs Barail (offset)"}],
 "why_relevant": ["Same formation at current TVD","TVD difference 22 m","1.8 km away","3 similar historical events"],
 "depth_range": {"min_md":0,"max_md":3120,"max_tvd":3045},
 "relevant_events": [EventBrief],
 "event_count": 4,
 "document_count": 3,
 "source_availability": {"documents":3,"with_evidence":4,"coverage":"PARTIAL"},
 "selected": false
}
```

### GET `/api/v1/wells/{well_id}/events`
Query: `event_type, tvd_min, tvd_max, limit`
→ `{items:[EventDetail]}`

### GET `/api/v1/wells/{well_id}/timeline`
Query: `window_md` (metres above/below current MD, default 300), `event_type`
Depth-aware merged timeline across the current well AND relevant offset wells:
```json
{
 "current_well": WellSummary,
 "window": {"top_md":1350,"bottom_md":1650,"tvd_at_current":1500},
 "current_marker": {"md":1500,"tvd":1500,"formation":"Barail","label":"YOU ARE HERE"},
 "entries": [ TimelineEntry ],
 "data_provenance":"SYNTHETIC_PROTOTYPE"
}
```
`TimelineEntry`:
```json
{"id":"EV-0007","kind":"drilling_event","origin":"OFFSET","well_id":"DEMO-05","well_name":"DEMO-05",
 "event_type":"STUCK_PIPE","event_label":"Stuck Pipe","md":1495,"tvd":1498,"formation":"Barail",
 "severity":"HIGH","severity_score":0.8,"occurred_at":"...","description":"...","mitigation":"...",
 "delta_from_current_md":-5,"relevance_note":"5 m shallower than current depth","document_id":"DOC-014",
 "evidence_count":1}
```
`kind` ∈ `drilling_event | formation_transition | current_marker | mitigation`.

### GET `/api/v1/events`
Query: `well_id, event_type, formation, tvd_min, tvd_max, severity_min, near_well_id,
radius_km, limit, offset`
→ `{items:[EventDetail], total}`
`EventDetail`:
```json
{"id","well_id","well_name","event_type","event_label","event_subtype","md","tvd","formation",
 "severity","severity_score","occurred_at","description","mitigation","status","days_open",
 "document":{"id","doc_type","doc_type_label","title","filename","doc_date","page_count","source_system"},
 "evidence_count":1,"data_provenance":"SYNTHETIC_PROTOTYPE"}
```

### GET `/api/v1/events/{event_id}`
Same as item + `evidence:[EvidenceBrief]` + `correlated_events:[EventBrief]` (events in
other wells within `tvd ± 50 m` with same type).

### GET `/api/v1/evidence/{event_id}`
Audit chain (Alert → Reason → Event → Well → Document → Evidence):
```json
{
 "event": EventDetail,
 "chain": {"alert_ids":["AL-003"],"reasons":[{"alert_id":"AL-003","reason":"...","factors":[...]}],
           "well": WellSummary, "document": {...} | null, "evidence":[EvidenceBrief]},
 "document": {...} | null,
 "context": {"previous_event": EventBrief|null,"next_event": EventBrief|null,
             "document_excerpt": "…actual stored text…"},
 "evidence": [ {"id","page":43,"section":"Day 12 – Drilling Summary","text_span":"…",
                "confidence":0.93,"bbox":null,"extraction_method":"RULE_TEMPLATE_SIMULATED"} ]
}
```
`page` may be `null` — never invent one.

### GET `/api/v1/documents?well_id=&doc_type=&limit=`
`{items:[DocumentSummary]}` — `DocumentSummary`:
`{id,well_id,well_name,doc_type,doc_type_label,title,filename,doc_date,source_system,
page_count,event_count,evidence_count,ocr_engine,extraction_method,is_simulated}`

### GET `/api/v1/documents/{doc_id}`
`{...DocumentSummary, excerpt, sections:[{heading,page,text}]}`

### POST `/api/v1/documents/ingest`
Accepts JSON **or** multipart. Purpose: run the prototype ingestion pipeline
(OCR → layout → sections → entities → events → depth normalization → formation mapping)
on an operator-supplied document, and persist what it extracts.
- JSON: `{"filename":"DDR-2024-118.txt","well_id":"DEMO-01","doc_type":"DDR","doc_date":"2024-05-12","text":"…","ocr_engine":"NONE"}`
- multipart: `file` + form fields `well_id`, `doc_type` (optional).
Response `201`:
```json
{"document": DocumentSummary, "pipeline": [{"stage":"ocr","status":"SKIPPED","detail":"No OCR engine installed; using embedded text layer","simulated":true}, ...],
 "extracted_events":[EventDetail], "sections":[{heading,page,text}], "warnings":["..."],
 "data_provenance":"OPERATOR_SUPPLIED_UNVERIFIED"}
```
If `well_id` is omitted the document is stored unattached (`well_id: null`) and a warning
is returned. Never crash on unparseable input — return `warnings`, `extracted_events: []`.

### GET `/api/v1/offset-replay/{well_id}`
Query: `radius_km`, `event_type`, `top_tvd`, `bottom_tvd`, `limit_wells`
```json
{
 "current_well": Well,
 "window": {"top_tvd":1200,"bottom_tvd":1800},
 "formation_column": [{"name":"Tipra","top_depth":0,"bottom_depth":1200,"current":false}, {"name":"Barail","top_depth":1200,"bottom_depth":1900,"current":true}, ...],
 "offset_wells": [ {"well":WellSummary,"distance_km":1.8,"relevance_score":0.86,"relevance_band":"HIGH",
                    "similarity":{...},"why_relevant":[...],
                    "events":[EventDetail], "formation_alignment":"SAME"} ],
 "current_events": [EventDetail],
 "recurring_hazards": [ {"event_type":"STUCK_PIPE","event_label":"Stuck Pipe","well_count":3,"event_count":4,
                         "tvd_interval":{"top":1482,"bottom":1510},"depth_below_current_m":null,
                         "wells":["DEMO-02","DEMO-05","DEMO-07"]} ],
 "alerts": [AlertBrief],
 "method": "PROTOTYPE_HEURISTIC",
 "data_provenance": "SYNTHETIC_PROTOTYPE"
}
```

### GET `/api/v1/alerts?well_id=&status=&severity=`
`{items:[AlertBrief], counts:{OPEN:4,...}}`
`AlertBrief`:
```json
{"id","current_well_id","current_well_name","event_type","event_label","title","severity_band",
 "risk_score","current_tvd","interval":{"top_tvd":1482,"bottom_tvd":1510},
 "formation":"Barail","formation_match":"SAME","supporting_well_count":3,"supporting_event_count":4,
 "distance_to_interval_m":8,"status","created_at","is_active":true,"headline":"..."}
```

### GET `/api/v1/alerts/{alert_id}`
Engineer decision panel payload — the richest endpoint:
```json
{
 "alert": AlertBrief,
 "rule": {"rule_id":"RULE_STUCK_PIPE","version":"rule-engine-0.1.0","method":"PROTOTYPE_HEURISTIC",
          "config":{"min_support_wells":2,"tvd_tolerance_m":60,"min_relevance":0.25,"formation_match_required":true,"weights":{...}}},
 "decision": {
   "q1_what_happened": {...markdown-free structured text + event refs...},
   "q2_why_relevant_now": [...],
   "q3_supporting_wells": [OffsetWell-lite],
   "q4_evidence": [EvidenceBrief + document + well + alert_id],
   "q5_historical_mitigation": [{"text":"Reduced ROP to 8 m/h ...","source_event_id":"EV-0007","source_document":"DDR-17 p.43","provenance":"SOURCE_DOCUMENT"}],
   "q6_what_to_review": [{"text":"...","provenance":"MODEL_GENERATED"|"SOURCE_DOCUMENT"|"SYSTEM_RULE"}]
 },
 "risk_factors": [{"code","label","value","weight","contribution","detail"}],
 "risk_breakdown": {"historical_event_match":0.35,"depth_proximity":0.25,"formation_similarity":0.2,"nearby_well_support":0.15,"operational_similarity":0.05,"total":1.0},
 "supporting_events": [EventDetail],
 "evidence_chain": [ {"step":"ALERT","ref":"AL-003","detail":"..."} , {"step":"REASON",...}, {"step":"EVENT",...}, {"step":"WELL",...}, {"step":"DOCUMENT",...}, {"step":"EVIDENCE",...} ],
 "actions": [EngineerAction],
 "generated_summary": {"text":"...","provenance":"RULE_BASED_TEMPLATE"|"MODEL_GENERATED","model":null,"disclaimer":"Prototype heuristic output. Not an operational instruction."}
}
```
`provenance` is mandatory on every generated sentence. `MODEL_GENERATED` only when an LLM
is actually configured.

### POST `/api/v1/alerts/{alert_id}/acknowledge`
Body `{"engineer":"A. Sharma","note":"Reviewed with rig team..."}` → returns updated
`AlertBrief`-plus actions. Idempotent-safe (re-acknowledge records a second action).

### POST `/api/v1/alerts/{alert_id}/notes`
Body `{"engineer":"...","note":"..."}` → 201 EngineerAction.

### POST `/api/v1/alerts/{alert_id}/status`
Body `{"engineer":"...","status":"ACKNOWLEDGED|DISMISSED|CLOSED|OPEN","note":""}` → updated alert.

### POST `/api/v1/search`
Body:
```json
{"query":"What happened around 1500 m TVD?","current_well_id":"DEMO-01",
 "filters":{"radius_km":8,"event_type":null,"formation":null,"tvd_min":null,"tvd_max":null},
 "limit":20}
```
Response:
```json
{
 "query":"…",
 "parsed_intent": {"event_type":["STUCK_PIPE"],"formations":["Barail"],"tvd_anchor_m":1500,
                   "radius_km":8,"near_current_well":true,"intent":"HAZARD_QUERY","explain":"Matched 'stuck pipe' event vocabulary; parsed 1500 m as a TVD anchor."},
 "structured_results": {
   "events":[EventDetail], "wells":[OffsetWell-lite], "documents":[DocumentSummary],
   "mitigations":[{"text","provenance":"SOURCE_DOCUMENT","event_id","well_name","document_ref"}],
   "evidence":[EvidenceBrief], "alerts":[AlertBrief]
 },
 "synthesis": {"text":"2-3 sentence summary assembled ONLY from retrieved records","provenance":"RULE_BASED_TEMPLATE",
               "model":null,"citations":[{"event_id","document_id","page","label"}],
               "disclaimer":"Generated from retrieved prototype records only."},
 "result_count": 11, "data_provenance": "SYNTHETIC_PROTOTYPE"
}
```
The UI renders `structured_results` as the primary answer and `synthesis` as a
secondary, clearly-labelled block. When a query has no parseable intent, fall back to
keyword scoring over events + documents and say so in `parsed_intent.explain`.

### GET/POST `/api/v1/config/relevance`
GET → `{weights:{...}, radius_km, min_relevance, weights_explanation:"Prototype defaults — not scientifically validated", version}`
POST body `{"weights":{...},"radius_km":8,"min_relevance":0.15}` → persists to DB
(`app_config` table) and **recomputes** the `offset_relation` cache. All weights 0..1 and
must sum to ~1 (tolerate ±0.05, else 422).

### GET/POST `/api/v1/config/risk`
GET → `{min_support_wells, tvd_tolerance_m, min_relevance, formation_match_required,
weights:{historical_event_match,depth_proximity,formation_similarity,nearby_well_support,operational_similarity},
severity_thresholds:{WARNING:0.45,HIGH:0.6,CRITICAL:0.78}}`
POST → same shape; recomputes alerts for all active wells (deterministic).

### POST `/api/v1/risk/recompute`
Body `{"well_id":"DEMO-01"}` → recomputes offset relations + alerts for that well,
returns `{"offset_relations_updated":n,"alerts_created":n,"alerts_updated":n,"alerts":[AlertBrief]}`.

### POST `/api/v1/demo/scenario`
Returns the deterministic demo bundle in one call (fast first paint for judges):
`{scenario:{...}, current_well, nearby_wells, replay, alerts, documents, event_types}`.
This is the same data the screens request individually.

---

## 4. Synthetic dataset requirements (Phase 1)

- 18 wells. One active well `DEMO-01` at MD 1500 m / **TVD 1500 m** in formation
  `Barail`, status `DRILLING`.
- ≥ 6 formations with depth ranges that stack monotonically, lithology + depositional
  environment metadata (realistic Upper Assam-style names are fine but fictional:
  Tipra, Barail, Lakwa, Moran, Bokaghat, Disang…).
- ≥ 70 drilling events across all 8 hazard types + transitions, all ≥ 400 m apart in a
  given well so the timeline is legible.
- **Anchored demo cluster** (must exist exactly, deterministic):
  - `DEMO-02` MUD_LOSS at TVD 1485 m (severity HIGH)
  - `DEMO-05` STUCK_PIPE at TVD 1498 m (severity HIGH)
  - `DEMO-07` STUCK_PIPE at TVD 1510 m (severity CRITICAL)
  - plus ≥ 1 more offset with STUCK_PIPE in 1482–1520 m so support ≥ 3 wells
  - all within 6 km of `DEMO-01`, all drilled through `Barail`.
- ≥ 40 documents: DDR per event day, WCR per completed well, ≥ 2 lessons-learned,
  ≥ 1 incident report. Every event has ≥ 1 evidence row with a real stored excerpt
  (generated deterministically from a template — the excerpt IS the stored text, so it is
  never "fabricated metadata").
- Every formation band maps TVD→formation by range lookup, not by hand-written per-well
  values.
- Generator is **deterministic** (`random.Random(seed)` with fixed seed 20240517, no
  wall-clock in stored records except explicit spud/completion dates).
- Wells spread over a fictional field with realistic lat/lon (upper Assam-ish
  ~26.4–26.8 N, 94.4–94.9 E) so a local map projection looks right.

---

## 5. Backend engineering rules

- SQLAlchemy 2.0 declarative, `Mapped[]` annotations. SQLite file `backend/data/pravah.db`
  (override with `PRAVAH_DATABASE_URL`). Schema portable to PostgreSQL/PostGIS; distance is
  computed in Python (`geo.py` haversine) — document this as a prototype simplification in
  `docs/ASSUMPTIONS.md`.
- Seed runs on startup if the DB is empty (idempotent). `PRAVAH_RESET_DB=1` forces reseed.
- No business logic in routers: routers validate + delegate to service modules.
- Every module has `__all__`-clean imports; no circular imports (services depend on
  models only).
- Tests: `backend/tests/` with pytest, using a temp SQLite DB (dependency override
  fixture). Must cover: relevance weighting math, alert rule firing/support counting,
  depth→formation mapping, evidence chain completeness for the anchored demo events,
  search intent parsing, and API smoke tests for every endpoint above.
- Run commands documented in `backend/README.md`.
