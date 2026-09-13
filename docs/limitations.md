# Limitations

HowReliable? currently provides domain, ingestion, EDA, mapping, conservative cleaning, and
target-agnostic descriptive features only; it cannot make reliability predictions. Future
work must account for at least the following constraints:

- Public automotive reliability data may be sparse, inconsistent, duplicated, or inaccurate.
- Complaints and voluntary owner reports introduce reporting and selection bias.
- Counts without complete vehicle-population and exposure data can give misleading rates.
- Model year, generation, powertrain, trim, options, geography, and usage may be ambiguous.
- Owner reports may omit maintenance history, driving conditions, or verified diagnoses.
- Predictions will contain uncertainty and may not generalize to underrepresented vehicles.
- Historical associations do not necessarily identify causes or an individual vehicle's state.
- Canonical component mapping is intentionally coarse and leaves many present values as
  `OTHER`; missing component values remain `UNKNOWN`.
- Event severity reflects explicit death, injury, crash, and fire indicators—not repair cost,
  mechanical damage, or failure probability.
- Missing mileage and dates remain common, and source zero values can have ambiguous meaning.
- A complaint is an allegation or observation, not necessarily a verified mechanical failure.
- Repeated ODI references across component rows remain separate when their row-level complaint
  identifiers differ.
- Canonical reliability events are observational evidence, not machine-learning ground truth.
- Full-corpus EDA found 64.176% mileage missingness, 4,552 explicit zero mileages, and 156
  values above one million; their meanings are unresolved.
- Six occurrence-to-report delays are negative, 1,018 exceed ten years, and the maximum
  exceeds 103 years. These records remain uncorrected.
- Canonical component mapping places 36.531% of mapped rows in `OTHER`, especially broad
  powertrain and modern driver-assistance source categories.
- Transmission and drivetrain are missing in 99.616% and 93.305% of rows respectively, so
  most complaints cannot carry a known Phase 1A configuration identity.
- Severity is highly imbalanced: 92.928% of mapped events are `LOW` under the explicit
  indicator rule.
- Complaint narratives include 441 values longer than the documented 2,048-character field
  size, and preserved source bytes may still have uncertain intended encoding.
- Phase 2B flags but does not resolve missing, zero, or extreme mileage; future model years;
  anomalous delays; broad components; short/oversized narratives; or encoding control codes.
- The clean artifact excludes non-vehicle products and unknown model years with explicit
  reason records. It is therefore an in-scope analytical view, not a replacement for the
  immutable source-faithful artifact.
- Complaint-volume exposure bias remains unresolved. Vehicle-population, registration,
  sales, fleet-size, and usage denominators are unavailable, so cohort complaint counts are
  not reliability or failure rates.
- Full vehicle configuration remains unavailable for 99.6202% of clean events, making broad
  make/model/model-year cohorts the best-supported current aggregation grain.
- Mileage remains missing for 63.6693% of clean events. Cohort mileage summaries expose their
  observed coverage and do not impute missing values.
- Whole-corpus cohort aggregates may include information later than a future prediction
  cutoff. They are descriptive and are not leakage-safe until a target and temporal
  observation policy exist.
- Canonical `OTHER` remains 36.5307% of accepted complaint events.
- Complaints are observational allegations rather than verified repairs or failures.
- Phase 2C artifacts are not a training dataset: they contain no target, prediction window,
  split assignment, imputation, encoding, scaling, or model-specific preprocessing.
- EWR production is only a production exposure proxy, not active fleet, registrations,
  vehicle-years, mileage, or usage. Different model years have unequal time at risk.
- The conservative Phase 2D match covers 44.7516% of complaint cohorts and 77.5753% of
  complaint events. Missing matches are unknown production, never reported zero production.
- EWR reporting scope, historical threshold changes, public filing revisions, and naming
  differences prevent uniform manufacturer/model coverage.
- `complaints_per_10k_produced` is a diagnostic reporting-volume ratio, not repair incidence,
  failure probability, reliability, or risk.
- Manufacturer communications describe notices, procedures, campaigns, warranty policy,
  software, and other manufacturer actions; their count is not a defect or failure count.
- One communication can expand across many products, years, and components. Document and
  applicability counts must remain separate.
- Only 1.5799% of the selected communication documents contain structured manufacturer
  component system/subsystem data, while 54.4% map at least one NHTSA component to the
  existing broad `other` category.
- Manufacturer document IDs repeat and the deprecated replacement field is empty, so Phase
  2E does not infer duplicate, revision, or supersession relationships.
- Exact communication matching covers 86.5417% of complaint cohorts and 98.5593% of
  complaint events, but absence means no matched document in this snapshot—not no issue.
- Recall campaigns are formal safety-defect or noncompliance actions, not observed failures,
  completed repairs, or evidence that every potentially affected unit failed.
- Recall matching covers 79.9344% of complaint cohorts and 95.5697% of complaint events;
  absence means no exact vehicle application in this snapshot, not no defect.
- Campaign-wide potentially affected population cannot be allocated safely to individual
  cohorts and is never summed across repeated application rows.
- Whole-history recall counts include campaigns after hypothetical prediction cutoffs and
  remain descriptive until future temporal feature generation applies those cutoffs.
- The Phase 2G integrated artifact is descriptive, not training-ready. Its whole-history
  source counts must be reconstructed at each future prediction cutoff.
- The 2020–2024 complaint window supports temporal future-report analysis but cannot supply
  complete early-life histories for older vehicle cohorts.
- No-report future windows are not verified healthy examples; issue occurrence, reporting,
  cohort exposure, and continued service are all unobserved.
- Phase 2G supports cohort-level complaint-activity target design only. It does not support
  individual-vehicle prediction or major-repair/failure probability.

Any future output will be decision support, not a replacement for a qualified mechanical
inspection, diagnosis, maintenance guidance, recall information, or safety advice.
