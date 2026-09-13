# Phase 2C Feature Engineering

Phase 2C derives deterministic, target-agnostic structured feature tables from the Phase 2B
clean artifact. The tables describe information present in the NHTSA complaint corpus. They
are not labels, reliability rates, risk estimates, or a training-ready dataset.

## Feature philosophy and grains

Production feature code reads only the Phase 2B clean JSON Lines artifact. It never reads the
raw or interim NHTSA source. Missing values remain missing, categories remain human-readable,
and source/event identifiers remain available for traceability.

Two grains are deliberately separate:

1. **Event grain:** one row for every accepted Phase 2B complaint event, preserving clean
   input order and stable event/source identity.
2. **Broad vehicle-cohort grain:** one row for each normalized make + normalized model +
   model-year combination, sorted by those three values. `broad_vehicle_id` is carried as the
   stable identifier and is checked for consistency. Configuration identity is not the grain
   because it is unavailable for nearly all records.

## Event-level schema

The event artifact has 42 columns in fixed order.

### Identifiers and categorical descriptors

- `event_id`, `source_type`, `source_record_id`, `source_reference_id`
- `broad_vehicle_id`, `known_configuration_id`
- `normalized_make`, `normalized_model`, `model_year`
- `component`, `severity`

The component and severity values use the existing domain enums. `known_configuration_id`
remains optional. Narrative and original source labels remain traceable in the clean artifact
but are not copied into the structured feature table.

### Date and vehicle-age values

- `report_date`, `report_year`, `report_month`
- `occurrence_date`, `occurrence_year`
- `vehicle_age_at_report_years`: `report_year - model_year`
- `report_before_model_year`
- `vehicle_age_at_occurrence_years`: `occurrence_year - model_year`
- `occurrence_before_model_year`
- `occurrence_to_report_delay_days`: `report_date - occurrence_date` in calendar days

Dates are ISO `YYYY-MM-DD`; month is 1-12. Age uses source calendar years rather than the
current wall clock. A negative age is not forced to zero: the age is `null` and its explicit
before-model-year status is `true`. If the required date is missing, the derived year, age,
and status are `null`. Report delay retains negative and extreme source-derived values because
their Phase 2B quality flags remain available.

### Mileage and severity evidence

- `mileage`, `mileage_unit`, `mileage_observed`
- `death_count`, `injury_count`
- `crash_positive`, `fire_positive`, `injury_positive`, `death_positive`

Mileage remains in miles and is never imputed or altered. Evidence indicators are `null` when
the corresponding evidence was unobserved; zero/`false` means an observed non-positive value.
Severity remains the Phase 1C safety-evidence category, not mechanical repair severity.

### Quality indicators

For every Phase 2B flag, the event schema contains `quality_<flag>` as a boolean:

- `broad_other_component`, `encoding_control_character`
- `extreme_mileage`, `extreme_report_delay`, `future_model_year`
- `missing_configuration_detail`, `missing_mileage`, `missing_narrative`
- `negative_report_delay`, `oversized_narrative`, `short_narrative`, `zero_mileage`

These columns describe evidence quality and coverage. They are not vehicle-risk indicators.

## Vehicle-cohort schema

The cohort artifact has 93 fixed-order columns.

### Cohort identity and coverage

- `broad_vehicle_id`, `normalized_make`, `normalized_model`, `model_year`
- `complaint_event_count`: source complaint rows observed for the cohort
- `unique_event_count`: distinct stable event IDs
- `unique_source_reference_count`: distinct non-null ODINO references
- `first_observed_report_date`, `last_observed_report_date`
- `known_configuration_count`, `known_configuration_share`

All rows, including repeated ODINO component rows, contribute to `complaint_event_count`.
Counts are complaint-corpus quantities and are not normalized by vehicle population, sales,
fleet size, age, mileage, or usage.

### Component composition

For every canonical component, the schema contains:

- `component_<component>_complaint_count`
- `component_<component>_complaint_share`

The stable component set is `engine`, `transmission`, `electrical`, `cooling`, `suspension`,
`brakes`, `steering`, `fuel_system`, `hvac`, `exhaust`, `body`, `airbags_restraints`,
`tires_wheels`, `other`, and `unknown`. Shares divide a component count by that cohort's
`complaint_event_count` and therefore sum to one, subject only to floating-point precision.
They describe composition within observed complaints, not component failure rates.

### Severity and explicit evidence

For each of `unknown`, `low`, `moderate`, `high`, and `critical`:

- `severity_<severity>_complaint_count`
- `severity_<severity>_complaint_share`

Severity shares use the cohort complaint count and sum to one. No numeric severity score or
ordinal average is created.

For each of `crash`, `fire`, `injury`, and `death`:

- `<evidence>_evidence_observed_count`
- `<evidence>_positive_count`
- `<evidence>_positive_share_of_observed`

The evidence share denominator is only rows where that evidence was observed. It is `null`
when the observed count is zero, so unavailable evidence is not presented as an observed
zero.

### Mileage and report delay

- `mileage_observed_count`, `mileage_observed_share`
- `mileage_all_observed_median_miles`
- `report_delay_observed_count`, `report_delay_observed_share`
- `report_delay_median_days`

Medians use all observed values. Extreme mileages are deliberately retained in the primary
descriptive statistic and remain separately countable through
`quality_extreme_mileage_count/share`. Missing mileage is not imputed. An even-sized median
may therefore be a half-mile floating-point value. Report-delay medians likewise retain
flagged negative and extreme values.

### Quality composition

For every Phase 2B quality flag, the schema contains:

- `quality_<flag>_count`
- `quality_<flag>_share`

The share denominator is the cohort's `complaint_event_count`. These values expose support and
data quality only.

## Temporal leakage warning

Cohort aggregates use the complete input artifact. They can contain observations later than
an eventual prediction date and are therefore **not leakage-safe for model training**. Phase
3 target design must define an observation cutoff and may need to regenerate cohort features
from time-bounded inputs. The implementation keeps row derivation and cohort accumulation
separate so a future cutoff can be added without changing the feature definitions.

No prediction windows, future-event counts, labels, splits, or time-dependent targets exist
in Phase 2C.

## Unavailable and deferred feature families

There are no recall, manufacturer-communication, registration, sales/population, fleet,
maintenance-history, or verified-repair features because those sources are not integrated.
Fake zero columns are not created. Narrative NLP, categorical encoding, scaling, imputation,
model-specific preprocessing, targets, and scores are also deferred.

Phase 2D evaluates NHTSA EWR production in separate exposure artifacts. It does not append a
denominator or diagnostic rate to either Phase 2C table. See [Exposure Data](exposure-data.md).

Phase 2E likewise evaluates manufacturer communications in independent document,
applicability, and cohort-diagnostic artifacts. Communication counts are not appended to the
Phase 2C schemas and are not training-ready features. See
[Manufacturer Communications](manufacturer-communications.md).

## Versioning, provenance, and execution

Feature version `nhtsa-complaint-features-1.0` is recorded with the source cleaning and mapping
versions, complete cleaning provenance, clean-artifact SHA-256, UTC generation timestamp,
row counts, exact schemas, policies, and output filenames, sizes, and checksums.

Run from the repository root after Phase 2B:

```console
python -m howreliable.data.features
```

The default outputs are:

- `data/processed/features/nhtsa-complaint-events.jsonl`
- `data/processed/features/nhtsa-vehicle-cohorts.jsonl`
- `data/processed/features/nhtsa-complaint-features.provenance.json`

CLI arguments can select explicit input and output paths. Existing targets are never
overwritten. With identical input, versions, policies, and clock, outputs are byte-for-byte
deterministic except for an intentionally current generation timestamp in a normal CLI run.

## Validated full-corpus result

The complete 412,866-row Phase 2B clean corpus produced 412,866 event rows and 10,670 broad
vehicle cohorts: 344 normalized makes and 2,446 normalized models. Event and cohort artifacts
have 42 and 93 columns and occupy 553,884,973 and 38,624,966 bytes respectively.

Report year, report month, occurrence year, and report delay are complete. Mileage is missing
for 262,869 events (63.6693%). Report age is null for 2,240 events (0.5425%) and occurrence
age for 4,266 (1.0333%) because the corresponding year precedes model year.

Among cohorts, complaint count has minimum 1, median 5, 75th percentile 22, 95th percentile
180, and maximum 2,884. There are 2,321 one-record cohorts (21.7526%). Mileage coverage has
median 36.3636%; 2,832 cohorts (26.5417%) have no observed mileage. These are corpus
diagnostics, not vehicle reliability comparisons.
