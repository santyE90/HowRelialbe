# Phase 4B complaint-activity presentation and decision contract

Phase 4B was originally planned as **Risk Scoring**. Target and evaluation validation showed
that name would overstate the evidence, so the milestone is formally reframed as
**Complaint-Activity Presentation and Decision Contract**. The model predicts observed
future complaint reporting, not mechanical failure, repair, reliability, safety, or an
individual vehicle outcome.

## Result semantics

`complaint-activity-result-1.0` is the authoritative immutable Pydantic schema. It represents
one normalized make/model/model-year cohort already present in the frozen Phase 3B feature
artifact; it does not accept arbitrary vehicle configurations. `identity` carries the cohort
ID, normalized make/model, model year, and explicit `make_model_model_year_cohort` grain.

`prediction.predicted_future_complaint_probability` is the estimated probability that the
cohort receives at least one accepted observed NHTSA complaint report from 2023-01-01 through
2023-12-31, given history through 2022-12-31. It is bounded in `[0,1]` and authoritative.
`predicted_future_complaint_percentage` is exactly probability times 100 for display; it is
not a score.

The frozen `.5` threshold converts that probability to the binary target prediction. It is
not a mechanical danger boundary and is identified as `frozen_phase_3_model_contract`.

| Classification | Presentation label |
|---|---|
| `OBSERVED_COMPLAINT_ACTIVITY_EXPECTED` | Complaint activity predicted |
| `NO_OBSERVED_COMPLAINT_ACTIVITY_EXPECTED` | No complaint activity predicted |

There are no multiple bands because no such cut points were scientifically validated.

## Explanation and evidence

The `explanation` section reuses Phase 4A's exact mean random-forest tree-path probability
contributions. It includes the baseline probability, up to five positive and five negative
drivers, transformed value, readable label, probability-space contribution, feature family,
and evidence source. Complete feature-family and evidence-source contribution maps are also
retained. Baseline plus all internal contributions reconstructs the frozen probability; the
compact top-driver lists are a presentation subset and are not expected to sum to it.
Contributions explain model behavior, not causation.

The factual `evidence` profile includes historical complaint support, cohort age, historical
years observed, days since the most recent complaint, manufacturer-communication
availability/status, and recall availability/status. It deliberately does not collapse this
evidence into a confidence metric.

Two deterministic flags reuse previously evaluated subgroups:

- `SPARSE_HISTORICAL_SUPPORT` applies when historical complaint support equals one. Its
  message states that historical evaluation is weak and drivers do not imply prediction
  confidence.
- `OLD_COHORT_WEAK_EVALUATION` applies when cohort age at cutoff is 21 years or more. Its
  message states the same limitation for that evaluated age subgroup.

No other warning threshold is invented. Every result embeds this general limitation:

> This estimate describes observed NHTSA complaint reporting for a make/model/model-year
> cohort. It is not a mechanical-failure, repair, reliability, safety, or individual-vehicle
> probability.

## Provenance and serialization

`provenance` binds the contract version, `PREFERRED` model identifier and checksum, target
version, feature/preprocessing contract, evaluation version/report checksum, explainability
version/report checksum, frozen threshold, and timezone-aware UTC generation instant. It does
not claim the model is production-ready.

Canonical serialization uses lexicographic object-key order, string enum values, finite JSON
numbers using Python's shortest round-trip representation, UTC ISO-8601 timestamps, explicit
null optional fields, UTF-8, two-space indentation, and a trailing newline. With the same
generation timestamp and inputs, duplicate result and artifact generation is byte-identical.

`evaluation_context` is optional and separate. Representative evaluation examples attach a
selection rule and observed target; an inference result neither needs nor fabricates future
ground truth.

## Representative behavior

`artifacts/presentation/representative-results.json` regenerates the six Phase 4A selections:
high-probability observed positive, low-probability observed zero, false positive, false
negative, support=1, and age-21+. These examples verify both labels, errors against observed
reporting, compact explanations, evidence profiles, and the two limitation flags. They do not
turn evaluation outcomes into inference inputs.

## Terminology and decision boundary

Safe language names the modeled event literally: predicted future complaint activity, a
feature contributed positively or negatively to the estimate, and evidence was or was not
observed. The historical or prohibited terms “reliability score,” “repair risk,” “failure
risk,” “safety score,” “risk level,” “high risk,” “low risk,” “confidence score,” “likely to
fail,” “buy,” and “avoid” must not appear as result claims. They may appear in documentation
or handoff metadata only to prohibit them.

There is no risk band, confidence score, purchase/repair/safety recommendation, or
prescriptive decision. “Decision” means only deterministic interpretation of the continuous
probability at the frozen binary-target threshold. People remain responsible for decisions,
and this output does not replace inspection, diagnosis, maintenance, recall, or safety advice.

## Phase 5A handoff

`artifacts/presentation/api-handoff.json` is the exact Phase 5A boundary. It identifies the
schema/version, model and feature/preprocessing contracts, evaluation and explanation
contracts, threshold, supported cohort grain, output semantics, mandatory limitation,
prohibited terminology, representative artifact checksum, absence of a future-ground-truth
requirement, and `PREFERRED`—not production-ready—status. Phase 5A may serialize this result;
it may not rename its probability, add bands/scores/recommendations, accept unsupported input
grain, or omit its limitation. No API is implemented in Phase 4B.

Phase 5A now implements that handoff through `howreliable-api-1.0`. It returns this result
directly for exact supported cohort IDs, omits evaluation context, and keeps model resources
loaded once per application. See [API documentation](api.md).
