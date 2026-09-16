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
- The Phase 3A binary zero means no accepted report was observed in a complete future
  complaint-source window; it is not evidence that a cohort was healthy or issue-free.
- Historical complaint volume is strongly associated with future reporting (2022/12m
  Spearman 0.7513), so popularity, exposure, and persistence may dominate a baseline.
- The selected target is not exposure-normalized. Available production denominators cover
  fewer than half of eligible cohorts and may reflect later filing revisions.
- The Phase 2G whole-history columns remain temporally unsafe for Phase 3B except for static
  cohort identity. Complaint, communication, and recall aggregates require cutoff rebuilds.
- Phase 3B uses one random cohort split at a shared 2022 cutoff. It does not demonstrate
  temporal generalization to another reporting era.
- The validation-selected forest is strongly stratified by historical support: test F1 is
  zero for one-complaint cohorts and .3030 for two-to-four-complaint cohorts.
- Test performance degrades for cohorts aged 21+ (ROC-AUC .7087, F1 .3030), and older
  cohorts have incomplete early-life history in the available complaint window.
- Communication and recall ablations add only modest signal beyond complaint history;
  source presence and matching coverage must not be interpreted as causal evidence.
- Make/model one-hot identity modestly improves held-out metrics but has a larger train-to-
  validation gap, consistent with some cohort-popularity memorization.
- Saved sklearn probabilities estimate only observed future complaint activity under this
  dataset and cutoff. They are not calibrated real-world failure or reliability risks.
- Phase 3C's `log1p` count transformation reduces skew but does not clip, winsorize, or
  resolve extreme or popularity-driven values.
- The PyTorch feature-quality audit is fitted to the frozen training split. Columns constant
  or duplicate at this cutoff may behave differently at another cutoff and require a new
  training-only audit rather than reusing current parameters.
- Deterministic loader order requires the explicit generator seed and zero-worker policy;
  exact behavior across materially different PyTorch/platform versions is not guaranteed.
- Phase 3D's selected MLP is stable across five seeds but slightly trails the random forest
  on ROC-AUC, PR-AUC, and Brier score. Its small F1 gains do not justify added complexity.
- The MLP still predicts no positives among test cohorts with exactly one historical
  complaint; age-21+ ROC-AUC falls to .6496. It does not resolve sparse or old-cohort bias.
- Ten-bin calibration shows local overprediction, while one-pass permutation sensitivity is
  affected by correlated feature families and must not be interpreted causally.
- Phase 3E resume occurs only at completed epoch boundaries and uses local files; it does not
  recover a partially completed batch or coordinate concurrent writers.
- Atomic replacement protects individual artifacts, not the entire multi-file run as one
  transaction. Cross-platform/device bitwise reproducibility remains outside PyTorch's
  guarantee even though pinned CPU reruns match exactly here.
- Phase 3F percentile intervals quantify sampling variability of the frozen cohort test set,
  not target-label noise, reporting bias, temporal transport, or causal uncertainty.
- Critical subgroup intervals are wide, and high-confidence errors remain errors against an
  observed-report target rather than verified evidence about mechanical condition.
- Phase 4A tree-path contributions exactly explain the forest computation but are not causal;
  correlated features redistribute importance, and sparse/old-cohort explanations inherit
  their weak evaluation performance.
- Phase 4B presents the same cohort-level reporting estimate; its percentage and binary label
  do not make it a mechanical boundary, reliability measure, or individual-vehicle claim.
- Phase 4B evidence availability is factual metadata, not confidence. Its two flags describe
  weak evaluated support=1 and age-21+ subgroups without quantifying certainty.
- The result contract supports only cohorts in the frozen Phase 3B feature artifact. Arbitrary
  user vehicle ingestion, temporal refresh, and API behavior remain outside this phase.
- Phase 5A exposes only exact frozen cohort IDs. It has no fuzzy or arbitrary-vehicle
  inference, database, authentication, remote artifact source, or deployment availability.
- Phase 5A factory creation requires every local contract artifact and checksum to validate;
  missing ignored artifacts prevent startup rather than degrading to partial predictions.
- Phase 5B checksum verification proves integrity against the local approved manifest, not
  safety of arbitrary pickle/joblib data. Only trusted project model bytes may be loaded.
- The Phase 5B registry is local and contains one explicit frozen bundle. It has no remote
  durability, promotion lifecycle, automatic latest selection, or production-readiness claim.
- Phase 6A S3 storage does not provide a deployment, availability SLA, cross-object
  transaction, concurrent-publisher lock, lifecycle/retention policy, replication guarantee,
  promotion alias, or production-readiness approval.
- Remote integrity depends on the frozen manifest and SHA-256 of downloaded bytes. It does
  not trust ETags and does not make hostile pickle/joblib safe.

Any future output will be decision support, not a replacement for a qualified mechanical
inspection, diagnosis, maintenance guidance, recall information, or safety advice.
