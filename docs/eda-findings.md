# Phase 2A EDA Findings

These findings describe the official NHTSA ODI complaints-received 2020–2024 corpus with
source SHA-256 `7ce0ae08713024ce3d5a078c99ef9ddebf62a5ca2e0aee81568d98ae8d44750d`.
They are observations about complaint records, not vehicle reliability rates, failure
probabilities, causal conclusions, or comparisons of vehicle quality.

## Observed facts

### Scale and identifiers

- The complete structured artifact contains 418,884 rows and 51 source columns and occupies
  about 1.1 GiB in the notebook's pandas representation.
- All 418,884 `CMPLID` values are unique; no exact duplicate rows were observed.
- There are 291,999 unique `ODINO` references. Some 217,232 rows belong to a repeated
  `ODINO`; 90,347 identifiers occur more than once, with at most nine rows for one value.
- Some 88,185 `ODINO` values have multiple distinct nonmissing component descriptions. This
  supports retaining component rows rather than deduplicating by `ODINO`.

### Missingness and vehicle coverage

- Make, model, and model year are each missing in seven rows. Component is missing in two.
- Mileage is missing in 268,824 rows (64.176%).
- Transmission is missing in 417,274 rows (99.616%), and drivetrain is missing in 390,839
  rows (93.305%). Exact Phase 1A configuration association is therefore rarely possible.
- Incident date, received date, crash, fire, injury count, death count, and narrative have no
  JSON nulls in this published artifact.
- Product types comprise 413,239 vehicle, 3,091 tire, 1,696 equipment, 851 child-restraint,
  and seven missing values.
- Vehicle rows contain 359 observed make strings, 2,505 model strings, and 10,747 distinct
  make/model/year combinations. These are unnormalized source labels.
- Ford accounts for the largest raw make representation at 72,081 records (17.443% of
  vehicle rows). This reflects complaint-corpus volume, not a reliability comparison.

### Mileage

- All 150,060 present mileage strings are syntactically parseable as unsigned integers.
- Median mileage is 68,000; the 25th and 75th percentiles are 27,000 and 110,000.
- There are 4,552 zero values, 337 values above 500,000, and 156 above 1,000,000.
- The maximum is 9,848,609. These values remain untouched and may represent valid extreme
  use, entry errors, unit issues, or upstream conventions; EDA cannot decide which.

### Dates and temporal coverage

- All incident and received-date strings parse under the documented `YYYYMMDD` format.
- Received dates span 2020-01-01 through 2024-12-31, with annual counts of 83,356; 69,128;
  76,628; 91,757; and 98,015 respectively.
- Incident dates span 1917-01-01 through 2024-12-31.
- The median occurrence-to-report delay is 11 days; 95% is 674 days and 99% is about 1,836
  days. Six delays are negative, 1,018 exceed ten years, and the maximum is 37,698 days.
- Model-year-to-report-year lag has a median of six years. There are 2,241 negative values
  and 182 above 30 years. The corpus includes 373 unknown model-year codes (`9999`) and 624
  records labeled model year 2025.

### ReliabilityEvent mapping

- 412,866 rows map successfully and 6,018 are rejected: a 98.5633% success rate.
- Rejections comprise 5,645 unsupported non-vehicle/missing product rows and 373 unknown
  model years. No malformed mileage, date, count, indicator, or identifier rejection was
  observed in this artifact.
- Canonical `OTHER` contains 150,823 rows (36.5307% of mapped events); `UNKNOWN` contains one
  row (0.0002%).
- The largest `OTHER` contributors are `UNKNOWN OR OTHER` (40,822), broad `POWER TRAIN`
  (37,976), `VEHICLE SPEED CONTROL` (10,996), `VISIBILITY/WIPER` (9,467), `EXTERIOR
  LIGHTING` (9,374), and several driver-assistance categories. Phase 2A does not change the
  conservative component rules.
- Other large canonical groups are engine (65,591; 15.887%), electrical (51,087; 12.374%),
  brakes (30,336; 7.348%), and steering (29,379; 7.116%). Counts indicate representation,
  not component failure rates.

### Severity evidence and narratives

- Derived severity is highly imbalanced: 383,669 `LOW` (92.9282%), 17,754 `MODERATE`
  (4.3002%), 11,051 `HIGH` (2.6767%), and 392 `CRITICAL` (0.0949%). No mapped event has
  `UNKNOWN` severity in this publication.
- Across all source rows, 19,125 report a crash, 8,015 report a fire, 11,417 have a positive
  injury count, and 411 have a positive death count. These are source indicators, not repair
  severity or verified outcomes.
- Every row has a nonempty narrative. Length ranges from 1 to 2,105 characters with median
  499; 908 are ten characters or shorter, and 441 exceed the dictionary's nominal 2,048
  character field size.

### Representation imbalance

- `OTHER` is the largest canonical component at 36.5307%.
- `LOW` is the largest severity class at 92.9282%.
- The largest source make accounts for 17.4429% of vehicle rows.
- These skews, together with unmeasured vehicle population/exposure, constrain later target
  design and evaluation. They do not establish reliability differences.

## Future hypotheses and Phase 2B follow-up candidates

The following are questions for later cleaning policy, not decisions made by EDA:

- Determine how to flag or interpret zero and extreme mileage without deleting evidence.
- Define treatment for negative, multi-decade, and otherwise suspicious reporting delays.
- Investigate future model-year labels and the `9999` unknown-year sentinel.
- Establish auditable text-encoding handling for preserved Latin-1 control-code artifacts.
- Review high-volume `OTHER` component roots and decide whether evidence justifies controlled
  taxonomy changes; do not automatically equate broad `POWER TRAIN` with transmission.
- Establish source-label normalization rules for make/model values while retaining originals.
- Decide how repeated `ODINO` component rows should be represented in analytical datasets
  without losing source-row identity.
- Flag narratives beyond documented size and very short narratives without performing NLP.
- Preserve explicit warnings that complaint volume lacks sales, fleet, age, mileage, and
  usage exposure denominators.

Phase 2B adopted conservative policies for these candidates without changing the historical
findings above; see [Cleaning Rules](cleaning-rules.md).
