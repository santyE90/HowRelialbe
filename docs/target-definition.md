# Phase 3A target definition

## Selected target

The first modeling target is **`future_12m_complaint_activity`**: for an eligible normalized
make/model/model-year cohort at the 2022-12-31 cutoff, predict whether at least one accepted
NHTSA complaint is reported from 2023-01-01 through 2023-12-31 inclusive. In the versioned
artifact this is the `future_any_complaint` column with `horizon_months=12`.

This target measures observed future NHTSA complaint reporting activity. It is not repair
probability, confirmed failure, mechanical-breakdown probability, individual-vehicle risk,
reliability, or safety probability. No model, feature matrix, split, preprocessing, or metric
is created in Phase 3A.

## Prediction unit and source

The unit is one normalized make + normalized model + model year cohort. Individual-vehicle
labels are impossible because no longitudinal vehicle identities or non-complaint vehicle
records exist.

Only accepted Phase 2C complaint-event features supply outcomes. `report_date` is the event
timestamp because it represents when a report became observable to the data system.
`occurrence_date` may precede reporting substantially and is not used for target timing.
Recalls, manufacturer communications, production, text, and external data never define the
target.

## Cutoff, window, eligibility, and censoring

For cutoff `C` and horizon `H`:

- History is `report_date <= C`.
- The future window is `C < report_date <= C + H`.
- The selected window is 2023-01-01 through 2023-12-31 inclusive.

A cohort is eligible only if its identity is valid, its model year is not after the cutoff
year, it has at least one accepted report on or before the cutoff, and the entire future
window lies inside the validated complaint corpus ending 2024-12-31. Production,
communication, and recall coverage are not required.

No additional prior-count or duration threshold is imposed. Such a threshold would change
the represented population, while the single-history-report rule already yields 8,416
cohorts. Phase 3B may perform sensitivity analysis, but may not select support rules merely
to improve metrics.

Incomplete windows are censored, not zero. Consequently 2023-12-31/24m is rejected before
any label artifact is written. Ineligible reasons are explicit:
`INCOMPLETE_FUTURE_WINDOW`, `INVALID_COHORT_IDENTITY`, `MODEL_YEAR_AFTER_CUTOFF`, and
`NO_HISTORICAL_COMPLAINT`.

For a binary target, zero means: **No complaint event was observed in the future NHTSA
complaint corpus during the defined window.** It does not mean no issue, failure, or repair
occurred, or that a cohort or vehicle was healthy or reliable.

## Artifact and versioning

Target definition version `future-complaint-activity-1.0` produces a target-only JSON Lines
artifact with this fixed schema:

```text
broad_vehicle_id, normalized_make, normalized_model, model_year,
target_definition_version, cutoff_date, horizon_months,
future_window_start, future_window_end, target_eligible, eligibility_reason,
cohort_age_at_cutoff, historical_complaint_count, future_complaint_count,
future_any_complaint, future_complaint_at_least_2,
future_complaint_at_least_3, future_complaint_at_least_5,
future_complaint_at_least_10, future_severe_complaint_count,
future_severe_complaint_activity, future_component_engine_complaint_count,
future_component_transmission_complaint_count,
future_component_electrical_complaint_count,
future_component_brakes_complaint_count,
future_component_steering_complaint_count
```

The artifact contains no model features. Provenance records the cutoff/window, eligibility
policy and accounting, corpus end, accepted-clean/event/integration versions and checksums,
severity and component rules, schema, distributions, bias diagnostics, timestamp, size, and
output checksum. Inputs are validated processed artifacts; no network access occurs.

```console
python -m howreliable.data.targets
python -m howreliable.data.targets --cutoff 2022-12-31 --horizon-months 24
```

Outputs are ignored under `data/processed/targets/` and cannot be overwritten.

## Full-corpus target distributions

Eligibility reasons below exclude the eligible count itself. `MY` means model year.

| Cutoff / horizon | Eligible | Ineligible: future MY / no history | Future events | Zero | >=1 / rate | >=2 | >=3 | >=5 | >=10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2021 / 12m | 7,153 | 1,640 / 1,877 | 66,467 | 3,114 | 4,039 / 56.47% | 3,297 | 2,816 | 2,055 | 1,262 |
| 2021 / 24m | 7,153 | 1,640 / 1,877 | 138,553 | 2,404 | 4,749 / 66.39% | 4,058 | 3,603 | 2,856 | 2,006 |
| 2022 / 12m | 8,416 | 994 / 1,260 | 80,505 | 4,018 | 4,398 / 52.26% | 3,547 | 3,046 | 2,269 | 1,440 |
| 2022 / 24m | 8,416 | 994 / 1,260 | 158,784 | 3,320 | 5,096 / 60.55% | 4,306 | 3,784 | 3,017 | 2,119 |
| 2023 / 12m | 9,575 | 468 / 627 | 88,015 | 5,084 | 4,491 / 46.90% | 3,646 | 3,080 | 2,304 | 1,502 |

| Cutoff / horizon | P50 | P75 | P90 | P95 | P99 | Mean | Population variance | Population SD | Max |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2021 / 12m | 1 | 6 | 20 | 43 | 151.48 | 9.292 | 930.466 | 30.504 | 968 |
| 2021 / 24m | 3 | 11 | 41 | 92 | 323.40 | 19.370 | 3,873.152 | 62.235 | 1,633 |
| 2022 / 12m | 1 | 5 | 20 | 44 | 164 | 9.566 | 1,203.938 | 34.698 | 1,384 |
| 2022 / 24m | 2 | 10 | 39 | 90 | 334.25 | 18.867 | 4,682.700 | 68.430 | 2,506 |
| 2023 / 12m | 0 | 4 | 19 | 43 | 157.26 | 9.192 | 1,274.666 | 35.702 | 1,122 |

Every valid provenance file also records model-year and exact-age distributions. The
2023/24m combination is unavailable because its end date is 2025-12-31.

## Primary threshold and horizon comparison

For 2022/12m the positive rates at thresholds 1, 2, 3, 5, and 10 are respectively 52.2576%,
42.1459%, 36.1930%, 26.9606%, and 17.1103%. `>=1` is not trivial: it supplies 4,398
positives and 4,018 observed-zero negatives with the clearest semantics. Higher thresholds
discard observable activity and make the target increasingly arbitrary, so they remain
diagnostics.

The 12-month horizon is selected over 24 months. Both are completely observed, but 12 months
has a simpler operational interpretation, less elapsed-time heterogeneity, and marginally
less concentration: its top 1% and top 10% of cohorts contain 27.48% and 77.43% of future
events, versus 27.90% and 77.89% for 24 months. The 24-month `>=1` rate is 60.55%, so its
class support is still usable but less balanced. Count prediction is deferred: the 2022/12m
count variance of 1,203.94 greatly exceeds its mean of 9.57 and volume is highly concentrated.

## Severe and component diagnostics

For 2022/12m, 1,571 cohorts (18.67%) contain at least one severe event and there are 5,538
moderate/high/critical events. Severe activity is safety-focused and more imbalanced, so it
is secondary rather than the first target.

| Cutoff / horizon | Severe-active cohorts | Engine | Transmission | Electrical | Brakes | Steering |
|---|---:|---:|---:|---:|---:|---:|
| 2021 / 12m | 1,427 | 1,917 | 235 | 1,958 | 1,373 | 1,098 |
| 2021 / 24m | 2,029 | 2,557 | 305 | 2,643 | 1,962 | 1,650 |
| 2022 / 12m | 1,571 | 2,111 | 142 | 2,245 | 1,546 | 1,261 |
| 2022 / 24m | 2,104 | 2,758 | 245 | 2,869 | 2,072 | 1,731 |
| 2023 / 12m | 1,450 | 2,251 | 167 | 2,278 | 1,554 | 1,231 |

Component columns report active cohort counts here; corresponding future event totals remain
in each checksummed provenance file.

| Component | Future events | Active cohorts | Eligible-cohort share |
|---|---:|---:|---:|
| Engine | 14,145 | 2,111 | 25.08% |
| Transmission | 279 | 142 | 1.69% |
| Electrical | 9,868 | 2,245 | 26.67% |
| Brakes | 6,462 | 1,546 | 18.37% |
| Steering | 5,538 | 1,261 | 14.98% |

Component targets are narrower, mapping-dependent, and sometimes sparse—especially
transmission—so they remain diagnostics for later study.

## Age, popularity, and exposure bias

At the 2022 cutoff, `cohort_age_at_cutoff = 2022 - model_year`; negative ages are ineligible,
not corrected. Twelve-month `>=1` prevalence is 68.80% at age 0, 53.48% at ages 1–2,
53.97% at 3–5, 56.65% at 6–10, 52.82% at 11–20, and 21.49% at 21+. Age is therefore a
material association that Phase 3B must evaluate explicitly.

Historical and future complaint counts are strongly associated for 2022/12m: Pearson
0.7333 and Spearman 0.7513. A baseline can easily learn persistence and cohort popularity
rather than a mechanically meaningful phenomenon. The top 10% concentration reinforces
this warning; Phase 3A does not attempt to remove it.

Production sensitivity covers only 4,055 of 8,416 eligible cohorts. Within them, raw future
count has median 3 and mean 15.58; the per-10k-produced diagnostic has median 0.791, but
extreme values become unstable for tiny production denominators. Production units correlate
with raw future count at Pearson 0.3083 and Spearman 0.3832. Production is incomplete and is
not active fleet exposure, so normalized activity is neither the primary label nor proof of
failure incidence.

## Historical feature leakage audit

The current integrated artifact is whole-history and must not be supplied directly to a
model at the 2022 cutoff.

| Classification | Columns/families | Phase 3B requirement |
|---|---|---|
| Static | Cohort ID, normalized make/model, model year | May be joined after identity validation |
| Safe only if rebuilt as-of cutoff | Complaint counts/dates/components/severity/evidence/mileage/quality; communication counts/types/components/dates; recall counts/types/components/dates | Reaggregate only records observable on or before 2022-12-31 |
| Unsafe whole-history aggregate | Every current complaint, communication, or recall aggregate; source support indicators; all-four/category fields; whole-history complaints-per-10k | Never use the Phase 2G value directly |
| Source limitation | Production counts, record IDs, periods, match metadata | Restrict or omit unless the historical filing/revision state can be justified |

Communications are available only when NHTSA `date_added <= cutoff`. Recalls are available
only when the Part 573 report-received date is on or before cutoff. Current production
snapshots may include later filings or revisions and cannot silently be treated as known in
2022. Phase 3B must create a separate as-of-cutoff feature artifact and verify that no target
window events contribute to it.

## Claims and rejected alternatives

Allowed: the target identifies whether an eligible cohort has at least one observed future
NHTSA complaint report in the specified 12-month source window. It may support models of
future complaint-report activity at cohort grain.

Not allowed: describing the target or later predictions as verified repairs, confirmed
failures, healthy vehicles, individual-vehicle probabilities, exposure-normalized failure
rates, safety probabilities, reliability scores, or causal effects.

The 24-month target remains a robustness outcome but was not selected. Thresholds `>=2`,
`>=3`, `>=5`, and `>=10` are interpretable count cutoffs but lack evidence-based superiority
over `>=1`. Count prediction is deferred because of overdispersion and concentration.
Severe and component targets are secondary due to narrower semantics and class support.
Repair/failure targets remain unobservable.

## Exact Phase 3B handoff

- Selected target: `future_12m_complaint_activity`, mapped to `future_any_complaint`
- Cutoff: 2022-12-31
- Horizon/window: 12 months, 2023-01-01 through 2023-12-31 inclusive
- Threshold: at least 1 accepted future complaint report
- Eligibility: valid cohort; model year <= 2022; at least one report on/before cutoff;
  complete future window
- Counts: 4,398 positive; 4,018 observed-zero negative; prevalence 52.2576%
- Artifact: `data/processed/targets/future-complaint-activity-2022-12-31-12m.jsonl`
- Version: `future-complaint-activity-1.0`
- SHA-256: `56b04f3f842d4f857f39b050c310b6fee2a83b20b9afeefc82c5c5967c19864e`
- Static join fields: cohort ID, normalized make/model, model year
- Rebuild as-of cutoff: all complaint, communication, and recall feature aggregates
- Restrict or omit: production values without defensible historical filing/revision state
- Prohibited input: every non-static whole-history column from the Phase 2G integrated row

Phase 3B may build leakage-safe features and baseline models. It must preserve the neutral
target semantics and must not reinterpret observed-zero rows as verified healthy cohorts.

## Phase 3B resolution

Phase 3B preserves this target contract exactly and aligns all 8,416 target identities to a
separate as-of-cutoff feature artifact. No target field enters a model pipeline. The frozen
split contains 5,891 train, 1,262 validation, and 1,263 test cohorts. Baseline probabilities
refer only to the defined observed complaint target. See
[baseline models](baseline-models.md) for the leakage rules, results, and limitations.

Phase 3C consumes this target only after verifying its exact checksum and cohort alignment.
The label is emitted as a separate float32 tensor of shape `[1]`; it never enters the feature
manifest or preprocessing fit. See [PyTorch data pipeline](pytorch-data-pipeline.md).
