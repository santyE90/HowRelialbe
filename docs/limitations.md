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

Any future output will be decision support, not a replacement for a qualified mechanical
inspection, diagnosis, maintenance guidance, recall information, or safety advice.
