# Limitations

HowReliable? currently provides foundational domain and ingestion code only and cannot make
reliability predictions. Future work must account for at least the following constraints:

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

Any future output will be decision support, not a replacement for a qualified mechanical
inspection, diagnosis, maintenance guidance, recall information, or safety advice.
