# Data Sources

## NHTSA ODI Vehicle Owner Complaints

The first source is the official National Highway Traffic Safety Administration (NHTSA)
Office of Defects Investigation Vehicle Owner Complaints dataset. NHTSA uses complaints with
other evidence to identify potential safety-defect trends and monitor recalls. It was chosen
because it is an official, documented, longitudinal source containing vehicle identity,
component, incident, and narrative fields—not because it is reliability ground truth.

- [NHTSA datasets and APIs](https://www.nhtsa.gov/nhtsa-datasets-and-apis)
- [Official complaint data dictionary](https://static.nhtsa.gov/odi/ffdd/cmpl/CMPL.txt)
- [Official 2020–2024 received-date archive](https://static.nhtsa.gov/odi/ffdd/cmpl/COMPLAINTS_RECEIVED_2020-2024.zip)
- [Official Excel import guide](https://static.nhtsa.gov/odi/ffdd/cmpl/Import_Instructions_Excel_All.pdf)

NHTSA describes the dataset as raw data collected through web and phone channels, covering
1949 to the present with a daily update frequency. The selected development artifact is the
official five-year set based on complaint received date for 2020–2024. NHTSA does not publish
an upstream version number, so provenance records `null` rather than inventing one.

### Verified format and schema

The archive contains a single headerless `.txt` file. Records are physical lines containing
51 tab-delimited fields; dates use `YYYYMMDD`. The current dictionary was updated April 30,
2026 and adds `STATE_OF_INCIDENT` and `VEHICLE_OPERATOR` as fields 50 and 51. The older May
2021 Excel guide still lists only 49 fields. The currently published 2020–2024 archive is
regenerated with all 51 fields, so adapter schema version `CMPL 2026-04-30 (51 fields)` is
enforced.

The source does not document text encoding. The real archive includes byte `0x81`, which is
undefined in Windows-1252. Ingestion therefore uses Latin-1 as a lossless byte-to-code-point
mapping rather than silently replacing undecodable input. Quote characters are literal data;
there is no documented CSV quoting layer.

Relevant source fields include complaint and ODI identifiers, manufacturer, make, model,
model year, crash/fire indicators, incident and received dates, injuries, deaths, component,
partial VIN, mileage, occurrence count, narrative, complaint type, drivetrain, fuel codes,
transmission, vehicle speed, product type, and other documented equipment/context fields.
All 51 fields are preserved in official order. Nonblank values remain source strings; blank
columns become explicit JSON `null`. Parsing does not coerce dates, numbers, flags, or codes.

### Storage and provenance

Official ZIP artifacts are immutable under `data/raw/nhtsa/complaints/`. UTF-8 JSON Lines and
their `.provenance.json` sidecars are written under `data/interim/nhtsa/complaints/`. Git
ignores both areas. Existing artifacts and outputs are never overwritten silently.

Provenance records the source organization and dataset, official URL, requested received-date
range, explicit upstream-version absence, retrieval and ingestion timestamps in UTC, source
filename and SHA-256 checksum, member filename, adapter/schema versions, record count, and
completeness. A bounded diagnostic output records its requested limit and is marked
incomplete.

### Limitations

- Complaints are self-reported and subject to reporting and selection bias.
- Complaint volume is not a vehicle failure probability or normalized reliability rate.
- Popular vehicles may naturally have more complaints because more are in service.
- Vehicle population, sales, exposure, age, mileage, and usage are not normalized here.
- Vehicle details and coded fields can be missing, inconsistent, or explicitly unknown.
- One ODI number may appear in multiple component records; ingestion does not deduplicate it.
- Complaint records are updateable, so the same ID may change in later published artifacts.
- Narratives are unstructured text and are preserved without classification or interpretation.
- Lossless Latin-1 decoding preserves uncertain bytes but does not resolve their intended
  character encoding.

Raw complaint counts must not be interpreted as reliability rankings or ground truth.
Phase 2B's non-destructive normalization, quality flags, and explicit retention policy are
documented in [Cleaning Rules](cleaning-rules.md). Phase 2D's separate exposure evaluation is
documented in [Exposure Data](exposure-data.md); it does not change the complaint source.

### NHTSA-to-event mapping

Phase 1C maps only vehicle products (`PROD_TYPE=V`) with valid `CMPLID`, `ODINO`, make, model,
and model year. Missing transmission or drivetrain prevents a Phase 1A configuration link
but does not discard the supported broad vehicle association. Dates and mileage are parsed
only when blank or unambiguously valid; malformed values produce categorized mapping errors.

Canonical component mapping uses explicit NHTSA component roots. Missing components become
`UNKNOWN`; present but unmapped values become `OTHER`, and the original value remains in the
event source reference. Severity uses only deaths, injuries, crash, and fire fields. Details
and limitations are documented in [Reliability Events](reliability-events.md).

## NHTSA Early Warning Reporting light-vehicle production

Phase 2D uses only NHTSA's official public EWR JSON API. The API exposes manufacturers,
period-specific report descriptors, and paginated light-vehicle production rows rather than
a single bulk file. Raw responses, official URLs, retrieval time, category and period,
SHA-256 checksums, and the absence of a published API schema version are recorded in an
immutable snapshot manifest. Full schema, cumulative-production semantics, bounded coverage,
and match results are documented in [Exposure Data](exposure-data.md).

## NHTSA Manufacturer Communications

Phase 2E uses NHTSA's official `TSBS_RECEIVED_2020-2024.zip` 14-field TSV as the canonical
rich communication source and audits `MFR_COMMS_RECEIVED_2020-2024.zip` as its compact CSV
view. They represent substantially overlapping views with different expansion grains and
small synchronization differences, so they are not unioned. Raw ZIPs and the official
`TSBS.txt` dictionary remain immutable with URLs, timestamps, SHA-256 checksums, archive
members, schema information, and complete processing counts. See
[Manufacturer Communications](manufacturer-communications.md).
