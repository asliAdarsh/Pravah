# Data sources — what Pravah actually runs on

No synthetic records. Every well, formation, event, evidence span and telemetry sample in the
database originates in one of the published files below, vendored under
`backend/data_sources/`.

## The corpus

| Source | Publisher | What it provides | Size |
|---|---|---|---|
| `NPD_Lithostratigraphy_member_formations_all_wells.xlsx` | Norwegian Petroleum Directorate, FactPages | Formation tops and UTM coordinates for Norwegian offshore wells — 119 wells with both, 85 distinct horizons | 0.16 MB |
| `NPD_Lithostratigraphy_groups_all_wells.xlsx` | NPD FactPages | Composite group tops, used where a well has no member tops | 0.08 MB |
| `NPD_Casing_depth_most_wells.xlsx` | NPD FactPages | Casing shoe depths — the casing/formation correlation on well records | 0.03 MB |
| `volve_ddrs_train.csv` / `volve_ddrs_test.csv` | Equinor Volve public DDR release | Real 24-hour operations logs, ≈30,000 reports, as time-stamped activity prose | 4.8 MB |
| `telemetry_15_9_F_9A.csv` | Equinor / NPD open data | High-frequency MWD and mud-logger telemetry for well 15/9-F-9A, 16,670 samples × 115 exported channels | 5.3 MB |

All are public open data. `GET /api/v1/meta → data_sources[]` serves this registry at runtime
with publisher, URL, licence and file size, and the UI renders it under Settings.

## What is loaded

| Entity | Count | Derived from |
|---|---|---|
| Wells | 119 + 1 active | NPD wells with both a coordinate and stratigraphy, plus the telemetry well |
| Formations | 85 | distinct NPD horizons |
| Documents | 400 (configurable to 4,000) | Volve DDR reports |
| Events | ~1,380 | hazard clauses extracted from the DDR text |
| Evidence spans | 1 per event | the verbatim line the event was read from |
| Telemetry samples | 16,670 | the exported measurements |
| Offset relations | 13,268 | derived by the relevance engine |
| Alerts | 4 | derived by the anomaly engine from the live telemetry |

Raise the report count with `PRAVAH_MAX_DDR_REPORTS`; the full corpus is ≈30k.

## The one derivation, and why it is labelled

The Directorate publishes **no lithostratigraphy or coordinate row for wellbore 15/9-F-9A**,
which is the well the telemetry belongs to. Rather than invent a position, two values are
derived from the five real wells in the same `15/9` block and the well record says so:

- **Position** — the mean published UTM of the 15/9 block wells, converted to WGS84.
- **Stratigraphic column** — the 15/9 block horizons, used for the formation band at any depth.

Both appear in the well's `status_note` and must surface as a proxy in the UI. An earlier
version hard-coded a published Volve field point instead; that put the live well **200 km** from
its own offsets and silently emptied the entire offset-correlation product. Deriving the
position from the block keeps the well in the same field as its offsets, which is the entire
point of the product.

## What the DDR corpus can and cannot support

The public Volve DDR release is published **per field, not per wellbore**. Reports do not say
which well they belong to. Pravah therefore:

- attaches every report to the field's live well and puts *"wellbore not published in this
  release"* in the document title, so no report is presented as a specific wellbore's record
  when the source does not say; and
- gives the **offset wells** real stratigraphy, casing and coordinates, but **no events** —
  because attributing field reports to them would be fabrication.

The consequence is honest and visible in the product: offset correlation is real geological
correlation, while the hazard layer is driven by the live telemetry and the field's real DDR
text rather than by per-well offset histories. Alerts raised from telemetry say so, and carry
`supporting_well_count: 0` because there is no per-wellbore evidence to point at.

## Never fabricated

| Rule | Where it is enforced |
|---|---|
| Gaps in telemetry are gaps. No forward-fill, no interpolation. | `datasources.load_telemetry` keeps `None`; the anomaly engine skips and resets CUSUM state across a gap |
| A formation is assigned by one canonical range query, used by the seeder, the API and the ingestion pipeline alike | `formations.formation_at_tvd` |
| An event's depth comes from the event's own line, not the first depth in the block | `ingestion.parse_report_text` |
| Evidence spans are substrings of the report they cite | asserted in the test suite |
| No lookahead in any statistic | `tests/test_telemetry_backtest.py`, truncation-equivalence tests |
| The dataset is never silently replaced by invented data | `seed_real` raises `RuntimeError` when the NPD export is missing |

## The measured performance claim

The one number in the app is the telemetry lead time, and it is reported with its uncertainty:

- first precursor **307.7 m MD** against the confirmed incident at **619.0 m MD**
- lead **311.3 m**, or **82 minutes** at the observed mean ROP — with the caveat, stated on the
  screen, that the mean is inflated by the fast surface interval (median ROP 52.2 m/h would
  give 358 minutes)
- **Wilson 95% interval 0.235–0.385** over 43 precursor episodes in 141 trials
- six leakage-audit checks green

No other accuracy, probability or lead-time figure is displayed anywhere.
