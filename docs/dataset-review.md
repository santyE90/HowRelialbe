# Phase 2G multi-source dataset review

## Checkpoint conclusion

The current data supports a cohort-level future **complaint reporting activity** prediction
problem with calendar-based observation cutoffs. It does not support individual-vehicle
failure prediction or a probability of major repair in the next 12 months. Complaints are
allegations, absence of a complaint is not a verified healthy outcome, production is an
incomplete exposure proxy, and no repair or maintenance outcome source exists.

Phase 3A should design a target around future complaint activity at make/model/model-year
grain, using an explicit calendar cutoff and language that never equates reports with repairs
or failures. No target or label is created in Phase 2G.

## Architecture and common grain

Phase 2G consumes validated processed artifacts only:

```text
Phase 2C complaint cohorts (base; 10,670 rows)
  + Phase 2D production matches
  + Phase 2E manufacturer-communication cohort evidence
  + Phase 2F recall cohort evidence
  -> descriptive integrated cohorts
  -> deterministic coverage, relationship, support, and temporal review
```

The grain remains normalized make + normalized model + model year. Every Phase 2C identity
is retained exactly once. Configuration grain is unsupported because configuration detail is
missing for 99.6202% of clean complaint events.

The ignored artifact `data/processed/integrated/howreliable-cohorts.jsonl` has 10,670 rows
and 168 columns. Its source-prefixed groups are:

- Four identity/base fields plus Phase 2C complaint counts, component/severity shares,
  mileage, timing, evidence, and quality diagnostics
- Production status, method, nullable units, record lineage, reporting periods, and the
  already validated complaints-per-10k-production diagnostic
- Manufacturer-communication status, unique-document/application counts, dates, structured
  type counts, component counts, and metadata coverage
- Recall status, unique-campaign/application counts, report dates, noncompliance, population
  coverage, remedy coverage, structured type counts, and component counts
- Descriptive source count, all-four indicator, and coverage category

It contains no target, label, risk score, reliability score, confidence score, split, encoded
feature, or imputed value.

## Join and missingness semantics

All three joins require exact equality of the complete Phase 2C cohort ID set; extra,
duplicate, or missing cohort identities fail integration. Input schemas, project versions,
and SHA-256 values are checked against Phase 2C–2F provenance before reading values.

Missingness has three distinct meanings:

| State | Integrated representation | Example |
|---|---|---|
| Observed zero | Numeric `0`, with source coverage true | Matched communication cohort with zero OTA documents |
| Source unmatched | Coverage false, source values `null` | No defensible production match |
| Source present, field absent | Coverage true, individual field `null` or an explicit observed-count value | Communication exists but dates/metadata are unavailable |

For unmatched communication and recall cohorts, their source count and component/type values
are null—not zero. For a matched source, zero within a structured subtype is a meaningful
observed zero. Production units are never imputed. Recall campaign-wide affected population
is not integrated or allocated to cohorts.

## Coverage and support

The integrated corpus contains 344 makes, 2,446 normalized models, model years 1970–2025,
and all 412,866 complaint events.

| Available sources | Cohorts |
|---:|---:|
| 1 (complaints only) | 525 |
| 2 | 1,698 |
| 3 | 4,501 |
| 4 | 3,946 |

Source availability is 4,775 production matches, 9,234 communication matches, and 8,529
recall matches. Missing counts are respectively 5,895, 1,436, and 2,141. All-four coverage
contains 3,946 cohorts and 308,345 complaints (74.6840% of events).

The eight exact combinations remain:

| Coverage | Cohorts | Complaint events |
|---|---:|---:|
| Complaints only | 525 | 1,824 |
| Complaints + production | 31 | 153 |
| Complaints + communications | 943 | 5,864 |
| Complaints + recalls | 724 | 2,637 |
| Complaints + production + communications | 642 | 10,450 |
| Complaints + production + recalls | 156 | 1,334 |
| Complaints + communications + recalls | 3,703 | 82,259 |
| All four | 3,946 | 308,345 |

Coverage by make and model year is recorded in `dataset-review.json`; it is not converted to
a score or used to remove sparse groups.

## Distributions and sparsity

| Diagnostic | Observed | Missing | Min | Median | P75 | P95 | Max |
|---|---:|---:|---:|---:|---:|---:|---:|
| Complaints per cohort | 10,670 | 0 | 1 | 5 | 22 | 180 | 2,884 |
| Production units | 4,775 | 5,895 | 1 | 34,236 | 83,889.5 | 252,021 | 721,238 |
| Communications | 9,234 | 1,436 | 1 | 36 | 103 | 464 | 1,295 |
| Recall campaigns | 8,529 | 2,141 | 1 | 3 | 5 | 11 | 42 |

There are 2,321 one-complaint cohorts. Mileage-observed share has median 36.3636%, and 2,832
cohorts contain no observed mileage.

Potential future support rules were evaluated but not enforced:

| Descriptive threshold | Cohorts remaining |
|---|---:|
| At least 2 historical complaints | 8,349 |
| At least 5 | 5,565 |
| At least 10 | 4,055 |
| At least 20 | 2,857 |
| At least 50 | 1,566 |
| At least 2 sources | 10,145 |
| At least 3 sources | 8,447 |
| All 4 sources | 3,946 |
| At least 5 complaints and 3 sources | 5,208 |

Phase 3A should consider a minimum prior-history rule to reduce unstable one-report examples,
but it must justify that rule from observation reliability and intended population—not model
performance. Requiring production would discard 55.2484% of cohorts and is not recommended
as a universal eligibility rule.

## Cross-source relationships

Correlations use complete source pairs only. They are descriptive and do not establish that
one source causes another or that larger counts imply lower reliability.

| Pair | Complete cohorts | Pearson | Spearman |
|---|---:|---:|---:|
| Complaints vs communications | 9,234 | 0.1753 | 0.3740 |
| Complaints vs recall campaigns | 8,529 | 0.2820 | 0.3119 |
| Complaints vs production units | 4,775 | 0.3852 | 0.4472 |
| Communications vs recalls | 7,649 | 0.1002 | 0.1427 |
| Production vs complaints-per-10k diagnostic | 4,775 | -0.0509 | -0.3854 |
| Model year vs complaints | 10,670 | 0.0974 | 0.0620 |
| Model year vs available-source count | 10,670 | -0.0751 | -0.1174 |
| Critical-severity complaints vs communications | 9,234 | 0.0216 | 0.0446 |
| Critical-severity complaints vs recall campaigns | 8,529 | 0.1079 | 0.0650 |

The positive complaint/production relationship is consistent with larger production cohorts
having more opportunities for reports, illustrating why raw complaint volume is exposure
sensitive. Communication and recall counts overlap in coverage but have weak count
correlation, supporting their retention as distinct evidence families. Component-pair
Spearman values are mostly weak to moderate; the largest recall relationship is complaint
airbag/restraint share versus recall airbag/restraint campaigns (0.3859). These patterns do
not establish causation or reliability.

## Source-specific limitations

- Complaints are self-selected allegations. They may be duplicated, delayed, incomplete, or
  affected by publicity, geography, owner behavior, fleet size, and time at risk.
- Production is cumulative manufacturing exposure, not active fleet, registrations,
  vehicle-years, usage, or mileage. Historical filings may be revised and only 44.7516% of
  cohorts have an exact match.
- Communications document manufacturer guidance and actions, not verified failures or
  repairs. Document and application counts must remain separate; structured manufacturer
  component metadata is sparse.
- Recalls are formal safety/noncompliance actions, not general reliability outcomes. A
  campaign may span many applications, and campaign population cannot be assigned to a
  specific cohort.

## Temporal availability and leakage

Whole-history integrated rows are deliberately descriptive and leaky for prediction. A
future training snapshot must be rebuilt as of its cutoff using:

- Complaints: `report_date` for observation availability; `occurrence_date` may support
  secondary incident timing but cannot substitute for when the report became known
- Communications: NHTSA `date_added`; manufacturer communication date is preserved but may
  precede public availability
- Recalls: Part 573 report-received date
- Production: reporting period, with an explicit limitation that current processed data
  cannot reconstruct the exact historical revision/publication state

Calendar year-end is the recommended initial strategy because it is deterministic, aligns
with the five-year complaint observation frame, and supports complete annual windows.
Vehicle-age-three cutoffs are not recommended: a 2020–2024 received-date corpus cannot
reconstruct early-life complaint history for older cohorts. Rolling monthly cutoffs are
technically possible for complaints/actions but add complexity while production is quarterly
and the observation span remains short.

“Full future observation” below means the complaint source calendar extends through the
window end for a cohort observed before the cutoff. It does not prove the cohort remained in
service, had known fleet exposure, or was a defensible negative example.

| Cutoff | Prior cohorts/events | ≥5 / ≥10 prior | Full 12m / future-report cohorts / events | Full 24m / future-report cohorts / events |
|---|---:|---:|---:|---:|
| 2021-12-31 | 7,241 / 150,934 | 3,444 / 2,311 | 7,241 / 4,113 / 70,341 | 7,241 / 4,827 / 146,059 |
| 2022-12-31 | 8,508 / 226,132 | 4,220 / 2,947 | 8,508 / 4,480 / 84,595 | 8,508 / 5,182 / 166,439 |
| 2023-12-31 | 9,672 / 316,466 | 4,920 / 3,573 | 9,672 / 4,572 / 91,066 | 0 / unavailable / unavailable |

All three cutoffs have a complete 12-month source window. The first two have complete
24-month windows; 2023 does not because complaint data ends 2024-12-31.

Source availability among cutoff-observed cohorts is:

| Cutoff | Production period ≤ cutoff | Communications added | Recalls reported |
|---|---:|---:|---:|
| 2021-12-31 | 2,646 | 6,175 | 5,745 |
| 2022-12-31 | 2,795 | 7,305 | 6,695 |
| 2023-12-31 | 2,904 | 8,312 | 7,579 |

The 2022 year-end cutoff is the strongest first Phase 3A candidate: it provides 8,508 prior
cohorts and complete 12- and 24-month windows through the end of the corpus. Phase 3A must
still choose the target definition, eligibility, support rule, and treatment of later-entering
cohorts.

## Negative examples and prediction unit

A cohort with no future complaint may have experienced no issue, experienced an unreported
issue, lacked meaningful exposure, left service, or simply not appeared in this received-date
slice. Complaint absence therefore means “no observed report,” not “healthy,” “no failure,”
or “no repair.” Any binary formulation must preserve this interpretation and evaluate
positive-unlabeled or count/ranking alternatives rather than claiming verified negatives.

The immediate problem must be cohort-level. Complaints have record IDs, not longitudinal
vehicle identities, and there is no table of individual vehicles that did not complain.
Mileage is event-level and mostly missing; it cannot create individual histories. Individual
vehicle risk claims are unsupported.

## Candidate target feasibility

| Candidate | Observable / timestamped | Positive support / imbalance | Defensible negatives | Leakage controllable | Exposure bias | Source dependency | Represents repair | Recommendation |
|---|---|---|---|---|---|---|---|---|
| Future complaint activity | Yes / yes, report date | Thousands / threshold-dependent | No; absence is only no report | Yes with cutoff rebuild | High | Complaint events | No | Recommended for Phase 3A design |
| Future severe complaint activity | Yes / yes | Present / high | Same complaint-absence problem | Yes | High | Complaint severity evidence | No | Secondary analysis only |
| Future component complaint activity | Yes / yes | Varies; broad `OTHER` is material / component-dependent | Same complaint-absence problem | Yes | High | Complaint component mapping | No | Possible later secondary target |
| Future recall/regulatory action | Yes / yes | Formal but narrower / must be quantified | No action does not prove no defect | Yes | Manufacturer/regulatory process bias | Recall campaigns and applicability | No | Valid different problem, not first choice |
| Relative elevated issue activity | Partly / yes | Potentially adequate / comparator-dependent | Relative comparator must be defined | Yes | Production incomplete | Complaints plus an exposure/comparator policy | No | Research candidate after exposure design |
| 12-month major repair/failure | No / no verified repair timestamp | Unknown / unknown | No | Not with current sources | Unresolved | Missing repair/maintenance outcomes | Intended, but unobserved | Unsupported |

## Recommended Phase 3A direction

Proceed to Phase 3A with a **cohort-level future complaint-activity target-design study**,
using calendar year-end cutoffs and initially evaluating the 2022-12-31 cutoff with 12- and
24-month windows. Phase 3A should compare count, thresholded activity, and ranking/relative
formulations without presuming that a binary zero is a healthy negative. The final target,
threshold, eligibility criteria, and observation policy remain Phase 3A decisions.

The outcome must be named and communicated as complaint/reporting activity. It cannot be
marketed as repair probability, failure probability, individual-vehicle risk, or overall
reliability.

## What the current data cannot support

- Verified major-repair or maintenance outcomes
- Individual-vehicle longitudinal prediction
- A defensible healthy/no-failure negative class
- Universal exposure-normalized rates
- Leakage-safe use of whole-history communication or recall counts
- Cohort allocation of recall affected population
- Complete configuration-, mileage-, or usage-specific risk
- Causal claims from source correlations
- A reliability, risk, or confidence score

These limitations do not prevent Phase 3A target design, but they materially constrain the
prediction problem that Phase 3A may define.

## Phase 3A resolution

Phase 3A subsequently selected cohort-level `future_12m_complaint_activity` at the
2022-12-31 cutoff with an at-least-one-report threshold. Its 8,416 eligible cohorts contain
4,398 positives and 4,018 observed-zero rows. This is a complaint-reporting outcome, not a
repair, failure, health, or reliability label. See [target definition](target-definition.md).
