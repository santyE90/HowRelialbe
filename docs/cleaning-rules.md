# Phase 2B Cleaning Rules

Phase 2B converts the immutable Phase 1B NHTSA complaint JSON Lines artifact into a
conservative analytical record. It does not repair source evidence. Original labels and
narratives remain available beside normalized or canonical values, and questionable values
are retained with named quality flags whenever a valid `ReliabilityEvent` can be constructed.

## Record retention and schema

Each accepted record represents one `CMPLID`. It contains the stable event ID, source type,
`CMPLID`, `ODINO`, broad and optional configuration vehicle IDs, original and normalized
make/model, original and parsed model year, canonical and original component, severity and
its four evidence fields, mileage and unit, occurrence/report dates, the unchanged narrative,
and a sorted list of quality flags. JSON object fields have a fixed order and missing values
are JSON `null`.

The cleaner does not deduplicate `ODINO`: repeated ODI references often represent distinct
component rows, so their distinct `CMPLID` events remain intact. A row that cannot become a
valid in-scope vehicle event is written to a separate exclusion JSON Lines artifact with its
relevant source values, stable mapping-failure reason, and message. The run enforces:

```text
input rows = clean rows + excluded rows
```

Vehicle rows with warnings are retained. Non-vehicle products are excluded as
`unsupported_product_type`; model year `9999` is preserved in the exclusion record and
excluded as `invalid_model_year`. Other mapper failures use the existing explicit reason
taxonomy.

## Cleaning rules

### Mileage

NHTSA's current dictionary names field 18 `MILES` and describes it as vehicle mileage at
failure. The cleaner records the unit as `miles`; the dictionary does not provide a separate
unit declaration beyond that field name. Missing mileage remains `null` and receives
`missing_mileage`. Zero remains zero and receives `zero_mileage`. Values strictly above
500,000 remain unchanged and receive `extreme_mileage`. This EDA-derived boundary is a
project quality diagnostic, not a claim that higher mileage is physically impossible. No
value is imputed, clamped, winsorized, or removed because of mileage.

### Model year and vehicle labels

The original year string is retained as `source_model_year`; valid years are also represented
as an integer. `9999` means unknown according to NHTSA and cannot satisfy the canonical event
vehicle identity, so it is explicitly excluded rather than replaced. A model year later than
the complaint received year remains unchanged and receives `future_model_year`. The cleaner
does not infer or correct years, and the Phase 1A `Vehicle` validation range is unchanged.

Original make and model strings are retained. Normalized values use the existing domain rule:
Unicode NFKC normalization, whitespace trim/collapse, and case folding. There is no fuzzy
matching, manufacturer alias table, brand merging, or guessed spelling correction.

### Dates

Parseable occurrence and report dates remain unchanged; missing dates remain `null`. A report
date before its occurrence date receives `negative_report_delay`. A delay strictly above
3,652 days receives `extreme_report_delay`, matching the Phase 2A greater-than-ten-year
diagnostic boundary. The threshold identifies a review candidate; it does not prove the date
is invalid. The cleaner does not shift, replace, or fabricate dates and does not emit elapsed
time as an ML feature.

### Components

The canonical taxonomy and Phase 1C mapping remain unchanged. Lowering `OTHER` prevalence is
not sufficient evidence for a semantic change. The following reviewed source roots remain
`OTHER` and receive `broad_other_component`:

- `POWER TRAIN` is broader than transmission and may involve several systems.
- `VEHICLE SPEED CONTROL` is not reliably one existing mechanical category.
- `VISIBILITY/WIPER` is broader than the existing exact HVAC defroster mapping.
- `EXTERIOR LIGHTING` has no exact existing canonical category.
- automatic emergency braking, adaptive cruise control, and forward collision warning are
  driver-assistance systems and cannot safely be forced into brakes or electrical.
- `SEATS` does not consistently mean body or restraints.
- electronic stability control spans control electronics and braking behavior.
- `UNKNOWN OR OTHER` remains explicitly broad rather than being guessed.

Consequently the full-corpus canonical `OTHER` share remains 36.5307% (150,823 of 412,866
accepted rows), identical to the Phase 2A mapping result. A later, evidence-backed taxonomy
revision may introduce a small cross-source category, but Phase 2B does not mirror NHTSA's
hierarchy or inspect narrative text.

### Narratives and encoding

Narratives are preserved exactly. A narrative of at most 10 characters receives
`short_narrative`; one above the dictionary's nominal 2,048-character size receives
`oversized_narrative`; a missing narrative receives `missing_narrative`. There is no
truncation, case conversion, punctuation removal, tokenization, classification, or NLP.

Phase 1B's Latin-1 decoding is a reversible byte-preservation policy for an undocumented
source encoding. The cleaner does not guess replacement characters. If original make, model,
component, or narrative text contains a C1 control code point (`U+0080`-`U+009F`), the record
receives `encoding_control_character`; the text itself remains unchanged.

### Configuration detail

`missing_configuration_detail` means the mapper could not establish a complete Phase 1A
configuration identity, normally because source transmission or drivetrain is absent. The
broad make/model/year association remains valid and the cleaner does not guess missing
automotive attributes.

## Quality-flag taxonomy

- `missing_mileage`, `zero_mileage`, `extreme_mileage`
- `future_model_year`
- `negative_report_delay`, `extreme_report_delay`
- `short_narrative`, `oversized_narrative`, `missing_narrative`
- `broad_other_component`
- `missing_configuration_detail`
- `encoding_control_character`

Flags are deterministic, may coexist, and describe source quality rather than predictive or
mechanical risk. Unknown year is an exclusion reason rather than a flag because no canonical
analytical event can be formed.

## Reproducibility and provenance

Run from the repository root after Phase 1B ingestion:

```console
python -m howreliable.data.cleaning
```

`--input`, `--output`, `--exclusions`, and `--provenance` can select explicit paths. Defaults
read `data/interim/nhtsa/complaints/complaints-2020-2024.jsonl` and write beneath
`data/processed/nhtsa/complaints/`. Existing targets are never overwritten.

The provenance sidecar embeds the complete source provenance and records mapping version
`nhtsa-reliability-event-1.0`, cleaning version `nhtsa-complaints-1.0`, policy thresholds,
filenames, UTC generation time, input/clean/exclusion counts, flag counts, and exclusion
reason counts. With identical input, versions, and clock, all three outputs are byte-for-byte
deterministic.

## Validated full-corpus result

The Phase 2B validation run used the complete 418,884-row 2020-2024 artifact with source
SHA-256 `7ce0ae08713024ce3d5a078c99ef9ddebf62a5ca2e0aee81568d98ae8d44750d`.
It produced 412,866 clean rows (98.5633%) and 6,018 exclusions (1.4367%). Exclusions were
5,645 `unsupported_product_type` and 373 `invalid_model_year`. Percentages below use accepted
rows as the denominator because flags belong to clean records.

| Quality flag | Count | Accepted rows |
| --- | ---: | ---: |
| `missing_configuration_detail` | 411,298 | 99.6202% |
| `missing_mileage` | 262,869 | 63.6693% |
| `broad_other_component` | 150,823 | 36.5307% |
| `encoding_control_character` | 47,315 | 11.4601% |
| `zero_mileage` | 4,546 | 1.1011% |
| `future_model_year` | 2,240 | 0.5425% |
| `extreme_report_delay` | 1,000 | 0.2422% |
| `short_narrative` | 864 | 0.2093% |
| `oversized_narrative` | 439 | 0.1063% |
| `extreme_mileage` | 337 | 0.0816% |
| `negative_report_delay` | 6 | 0.0015% |
| `missing_narrative` | 0 | 0.0000% |

Counts differ from raw-corpus EDA where the affected rows include exclusions. This is an
accounting difference, not source mutation.

## Explicit non-goals

The cleaner does not perform imputation, outlier deletion, deduplication, exposure
normalization, alias/fuzzy matching, feature engineering, target construction, text analysis,
data enrichment, model fitting, scoring, or inference. Complaint counts remain observational
source records, not reliability rates or verified failures.
