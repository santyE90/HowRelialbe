# Phase 3B baseline models

## Objective and target contract

Phase 3B predicts `future_12m_complaint_activity` (`future_any_complaint`) for a
normalized make/model/model-year cohort. The cutoff is 2022-12-31 and the observed future
window is 2023-01-01 through 2023-12-31. All 8,416 eligible cohorts have at least one
accepted historical complaint; 4,398 are positive and 4,018 are observed-zero. This is a
model of observed future complaint reporting, not repair, failure, safety, individual-
vehicle risk, health, or reliability.

## Leakage-safe reconstruction

The canonical 114-column feature artifact is rebuilt independently of the Phase 2G
whole-history integration. It contains one human-readable row for every target cohort and
no target column. Complaint events require `report_date <= 2022-12-31`. Communication
documents require NHTSA `date_added <= 2022-12-31` and are deduplicated by communication
identity. Recall campaigns require their earliest Part 573 report-received date to be on or
before the cutoff and are deduplicated by campaign identity. Recency windows are calendar
windows beginning 2022-01-01, 2021-01-01, and 2020-01-01 for 12, 24, and 36 months.

The generator verifies source and target checksums, target version and cutoff, schema,
unique identities, exact target alignment, and the historical complaint count already
recorded by the target. It refuses overwrites and writes deterministic, Git-ignored JSON
Lines plus provenance.

Production is excluded. The available EWR snapshots cannot reconstruct what filing or
revision state was known at the historical cutoff, so including their current values would
weaken the primary leakage contract. No production sensitivity model was run.

## Feature families and missingness

| Family | Canonical columns | Contents |
|---|---:|---|
| STATIC | 6 | cohort identity, model year, cutoff age and cutoff |
| HISTORICAL_COMPLAINT_VOLUME | 6 | counts, observed span, dates and last-report age |
| HISTORICAL_COMPLAINT_RECENCY | 3 | 12-, 24-, and 36-month counts |
| HISTORICAL_COMPLAINT_COMPONENTS | 30 | 15 component counts and shares |
| HISTORICAL_COMPLAINT_SEVERITY | 17 | severity, evidence and mileage summaries |
| COMMUNICATIONS | 28 | availability, dates, types and components |
| RECALLS | 24 | availability, dates, advisories, noncompliance and components |
| OPTIONAL_PRODUCTION | 0 | deliberately excluded |

Communication and recall status distinguishes `OBSERVED_RECORDS`,
`MATCHED_RECORDS_AFTER_CUTOFF_ONLY`, and `NO_MATCHED_RECORD`. A genuine historical zero is
kept as zero while date/recency values without an observed historical source record remain
null. On the full artifact, mileage median is null for 1,704 rows; communication date and
recency fields are null for 1,184; recall date and recency fields are null for 1,755.

Every sklearn pipeline is fit on training rows only. Numeric fields use median imputation
with missing-value indicators. Logistic regression also standardizes numeric fields. The
optional identity experiment one-hot encodes make and model with unknown categories ignored;
it does not use target encoding. Canonical date strings and source-status strings are kept
for auditability but model inputs use their explicit numeric/indicator counterparts.

## Split design and limitation

Seed `20220913` creates one deterministic, target-stratified 70/15/15 cohort assignment:

| Split | Rows | Positive | Prevalence |
|---|---:|---:|---:|
| Train | 5,891 | 3,078 | 52.2492% |
| Validation | 1,262 | 660 | 52.2979% |
| Test | 1,263 | 660 | 52.2565% |

Model candidates are selected on validation ROC-AUC, then PR-AUC and F1. Rule thresholds
are selected on validation F1, then ROC-AUC. The model classification threshold is fixed at
0.5. Test data is evaluated only after selection.
This same-cutoff random split measures discrimination among 2022-cutoff cohorts; it does not
test generalization across calendar eras. Temporal backtesting with earlier cutoffs remains
future work.

## Models, tuning, and metrics

The intentionally small candidate sets are logistic regression `C` in `{0.1, 1.0}`, two
bounded random-forest configurations, and histogram gradient boosting with learning rates
`0.05` and `0.1`. Seeds are fixed for Python, NumPy, and sklearn. Metrics are accuracy,
precision, recall, F1, ROC-AUC, PR-AUC, Brier score, confusion matrix, target prevalence,
and predicted-positive prevalence. Probabilities mean only
`predicted_future_complaint_probability`.

The trivial historical-any rule is degenerate because eligibility already requires a prior
complaint. The majority rule predicts every row positive. The count rule selected
`historical_complaint_count >= 4`; the recent rule selected `complaints_last_12m >= 2`.

## Full-corpus results

Test metrics below use the frozen test split. Confusion matrices are `[[TN, FP], [FN, TP]]`.
The machine-readable result preserves complete train, validation, and test metrics and every
selected hyperparameter.

| Experiment/model | Accuracy | Precision | Recall | F1 | ROC-AUC | PR-AUC | Brier | Pred. + | Confusion matrix |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Majority | .5226 | .5226 | 1.0000 | .6864 | .5000 | .5226 | .2495 | 1.0000 | `[[0,603],[0,660]]` |
| Count rule `>=4` | .7838 | .7699 | .8364 | .8017 | .7814 | .7294 | .2162 | .5677 | `[[438,165],[108,552]]` |
| Recent rule `>=2` | .7838 | .8146 | .7591 | .7859 | .7850 | .7443 | .2162 | .4869 | `[[489,114],[159,501]]` |
| Logistic: count | .7910 | .8822 | .6924 | .7759 | .8668 | .8908 | .1470 | .4101 | `[[542,61],[203,457]]` |
| Logistic: volume + recency | .8092 | .8451 | .7773 | .8098 | .8769 | .9091 | .1364 | .4806 | `[[509,94],[147,513]]` |
| Logistic: all complaint | .8131 | .8397 | .7939 | .8162 | .8796 | .9093 | .1362 | .4941 | `[[503,100],[136,524]]` |
| Complaint + communications | .8139 | .8422 | .7924 | .8165 | .8864 | .9132 | .1331 | .4917 | `[[505,98],[137,523]]` |
| Complaint + recalls | .8171 | .8432 | .7985 | .8202 | .8813 | .9105 | .1351 | .4949 | `[[505,98],[133,527]]` |
| Complaint + both | .8171 | .8432 | .7985 | .8202 | .8876 | .9139 | .1324 | .4949 | `[[505,98],[133,527]]` |
| Complaint + both + identity | .8298 | .8482 | .8212 | .8345 | .8958 | .9202 | .1273 | .5059 | `[[506,97],[118,542]]` |
| Random forest: complaint + both | .8187 | .8493 | .7939 | .8207 | .8928 | .9173 | .1295 | .4885 | `[[510,93],[136,524]]` |
| HistGradientBoosting: complaint + both | .8171 | .8410 | .8015 | .8208 | .8955 | .9204 | .1280 | .4980 | `[[503,100],[131,529]]` |

The random forest is selected by validation ROC-AUC: validation accuracy .8288, precision
.8737, recall .7864, F1 .8278, ROC-AUC .9060, PR-AUC .9293, Brier .1209, and
`[[527,75],[141,519]]`. Its train ROC-AUC is .9433 versus .9060 validation and .8928 test,
showing moderate overfit. Histogram gradient boosting has slightly better test ranking, but
test results were not used for selection.

## Ablations and persistence bias

For logistic regression on test data, adding volume/recency to count changes ROC-AUC by
+.0101, PR-AUC by +.0182, and F1 by +.0339. Adding richer complaint components/severity
then changes them by +.0028, +.0002, and +.0064. Relative to full complaint features:

| Addition | ROC-AUC change | PR-AUC change | F1 change |
|---|---:|---:|---:|
| Communications | +.0067 | +.0039 | +.0004 |
| Recalls | +.0017 | +.0012 | +.0040 |
| Communications + recalls | +.0080 | +.0046 | +.0040 |
| Make/model identity after both | +.0082 | +.0063 | +.0143 |

Communications add a small ranking/calibration signal but almost no test F1. Recalls add
very little ranking signal and a small F1 change. Identity helps modestly, but its train
ROC-AUC .9310 versus .9029 validation indicates some popularity memorization. Historical
support, unique complaint identity, observed duration, recency, and model age remain the
dominant pattern. The selected forest improves substantially over the threshold count rule
in test ROC-AUC (+.1115), PR-AUC (+.1879), and Brier score, but only +.0189 in F1.

## Interpretation and subgroup diagnostics

Validation permutation importance for the selected forest is led by unique historical ODI
count (.00409 mean ROC-AUC decrease), days since last complaint (.00318), historical years
observed (.00290), complaints in 12 months (.00227), complaints in 24 months (.00205), days
since last communication (.00177), model year (.00175), historical complaint count (.00175),
and cutoff age (.00147). Individual importance values are small and correlated features can
share importance; they do not establish causation.

| Test subgroup | Rows | ROC-AUC | F1 |
|---|---:|---:|---:|
| Age 0-2 | 220 | .8999 | .8480 |
| Age 3-5 | 232 | .9136 | .8843 |
| Age 6-10 | 309 | .9181 | .8571 |
| Age 11-20 | 396 | .8673 | .7775 |
| Age 21+ | 106 | .7087 | .3030 |
| Historical support 1 | 276 | .6638 | .0000 |
| Historical support 2-4 | 330 | .7063 | .3036 |
| Historical support 5-9 | 197 | .7520 | .7377 |
| Historical support 10-49 | 303 | .7924 | .9269 |
| Historical support 50+ | 157 | undefined (one class) | 1.0000 |
| Communications + recalls | 903 | .8931 | .8551 |
| Communications only | 188 | .8255 | .6763 |
| Recalls only | 97 | .7637 | .2500 |
| Complaints only/limited | 75 | .7481 | .4000 |

Performance does not hold uniformly. It degrades sharply for old and sparse cohorts; at the
0.5 threshold the forest finds none of the 45 positives among one-complaint test cohorts.
The 50+ bucket contains only positives, so its perfect classification does not measure
discrimination. Source groups also differ greatly in support and prevalence and should not
be read causally.

## Artifacts and reproducibility

- Features: `data/processed/modeling/cohort-features-asof-2022-12-31.jsonl`, 8,416 rows,
  114 columns, 45,560,455 bytes, SHA-256
  `1cffb203298af438639c102a924f205ffb8ec25e6ea87035e670cecc902dfdfe`.
- Split: `data/processed/modeling/cohort-split-2022-12-31.jsonl`, SHA-256
  `43211c19328054f21ed730da6b9e65b78fae6e4b0af5983411b2a6811b34297a`.
- Results: `artifacts/models/baseline-results.json`; serialized pipelines are under
  `artifacts/models/baselines/` with checksums recorded per entry.

The target, features, split, package versions, candidate sets, chosen parameters, timestamps,
and model checksums are recorded. Fixed-clock fixtures confirm byte-identical feature and
split serialization. sklearn estimators use fixed random states; exact model bytes can still
depend on the pinned platform/library build.

## Claims, limitations, and next recommendation

Supported: these features distinguish same-cutoff cohorts with and without an observed 2023
complaint reasonably well on one random held-out cohort split. Complaint persistence and
popularity supply most of the signal; communication/recall evidence adds only modest
incremental value.

Unsupported: repair/failure/reliability probability, individual-vehicle inference, causal
effects, exposure-normalized rates, temporal generalization, or equal performance across
ages/support levels. Observed zero remains no accepted report, not health. Reporting bias,
unknown fleet exposure, incomplete early history for old cohorts, source matching, and a
single calendar cutoff remain material limitations.

Proceed to Phase 3C only as a controlled engineering benchmark using the frozen split—not
because Phase 3B demonstrates a need for neural complexity. A neural model must beat the
validation-selected forest at validation ROC-AUC .905985, PR-AUC .929263, and F1 .827751,
then exceed its untouched-test ROC-AUC .892844, PR-AUC .917283, and F1 .820673 without
worsening sparse and old-cohort behavior. At the Phase 3B checkpoint, Phase 3C was not yet
implemented.

Phase 3C subsequently implemented the frozen-input Dataset/DataLoader boundary without
training a model. It preserves this comparison floor and the subgroup metadata needed for
an honest Phase 3D evaluation. See [PyTorch data pipeline](pytorch-data-pipeline.md).
