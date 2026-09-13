# Phase 2E NHTSA Manufacturer Communications

Phase 2E adds official manufacturer-side documentary evidence without turning a bulletin
into a complaint, failure, repair, label, or reliability judgment. It preserves document
identity separately from each vehicle applicability relationship and does no text analysis.

## Official source and purpose

NHTSA requires all motor-vehicle and equipment manufacturers, including low-volume and child
restraint manufacturers, to submit notices, bulletins, warranty or policy extensions, and
product-improvement communications about defects, failures, malfunctions, performance
failures, flaws, or unintended deviations whether safety-related or not:

- [NHTSA Manufacturer Communications](https://www.nhtsa.gov/vehicle-manufacturers/manufacturer-communications)
- [NHTSA datasets and APIs](https://www.nhtsa.gov/nhtsa-datasets-and-apis)
- [Official flat-file dictionary](https://static.nhtsa.gov/odi/ffdd/tsbs/TSBS.txt)
- [2020-2024 compact CSV](https://static.nhtsa.gov/odi/ffdd/tsbs/MFR_COMMS_RECEIVED_2020-2024.zip)
- [2020-2024 rich TSV](https://static.nhtsa.gov/odi/ffdd/tsbs/TSBS_RECEIVED_2020-2024.zip)

The files are grouped by when NHTSA received/added communications, not by vehicle model
year. Their applications span model years 1955-2026, and manufacturer communication dates
span 1989-06-30 through 2025-02-20 after two out-of-range source dates are retained but not
parsed. Three records have anomalous 2016 `Date Added to File` values inside the named range;
these are preserved rather than silently removed.

## Actual formats and relationship

Both ZIPs contain one UTF-8, LF-delimited member and have no published independent upstream
version. The shared `TSBS.txt` dictionary has a May 13, 2024 change log and describes the
current 14-field layout.

`MFR_COMMS_RECEIVED_2020-2024.csv` is quoted CSV with a five-column header:

`TSB/Document ID, Make, Model, Model Year, Concise Summary`

It has 431,498 parsed data records and four repeated header rows. Model Year can be a
comma-separated list. Its first-column values empirically correspond to the rich file's
NHTSA IDs despite the compact header label; this observed inconsistency is not reinterpreted
as manufacturer document identity.

`TSBS_RECEIVED_2020-2024.txt` is headerless TSV. All 2,406,749 rows have exactly 14 fields:

1. NHTSA ID Number
2. Replacement Service Bulletin Number
3. Date Added to File
4. TSB/Document ID
5. Mfr Communication Date
6. Mfr Internal Campaign ID/Software Version
7. Communication Type
8. Make
9. Model
10. Model Year
11. NHTSA Components
12. Mfr Component System
13. Mfr Component Subsystem
14. Summary

The official dictionary says the compact view is one row per communication/product, while
the rich flat file expands each multivalued product and component combination. The real
files confirm that relationship: 73,927 communication identifiers and 1,691,056 expanded
products overlap. The compact view has six identifiers and 636 products absent from TSV;
TSV has three identifiers and 64 products absent from the compact view. These small
publication-timing/data differences make a union unsafe. HowReliable? uses the richer TSV as
canonical and treats CSV solely as an overlap audit.

## Identity and canonical schemas

The stable NHTSA ID is namespaced as `nhtsa_mc_<id>` for communication identity. The
manufacturer's `TSB/Document ID` is preserved but is not assumed unique: 61,575 distinct
values occur across 73,930 NHTSA communications, and 7,711 nonblank document IDs occur on
more than one NHTSA ID. Those records are not deduplicated.

`CanonicalCommunication` preserves the NHTSA ID, manufacturer document and campaign/software
identifiers, original and canonical structured type, original and parsed dates, date quality
flags, summary, original NHTSA components, conservatively mapped shared components,
manufacturer component fields, raw artifact, and number of expanded source rows. The source
contains no reporting-manufacturer field or direct PDF URL, so neither is invented.

`CommunicationApplicability` represents one distinct NHTSA ID + original make + original
model + original model-year relationship. Its SHA-256 identity is separate from the shared
communication ID. It preserves originals, adds NFKC/whitespace/case-folded identity, and
records how many component-expanded TSV rows collapsed into the application. Model year
`9999` remains insufficient identity, never a guessed year.

## Structured classification and components

Only the official `Communication Type` field is mapped:

- `Service Bulletin/Repair Instructions` → `SERVICE_BULLETIN`
- `Service Campaign` → `SERVICE_CAMPAIGN`
- `Warranty Program/Extension` → `WARRANTY_PROGRAM`
- `Over The Air` → `OVER_THE_AIR`
- `Emissions` → `EMISSIONS`
- `Other` → `OTHER`
- blank or unrecognized values → `UNKNOWN`

No summary text, keywords, NLP, embeddings, or inferred semantics affect classification.
Original NHTSA and manufacturer component fields are retained. The existing exact-root NHTSA
component mapper is reused without expanding the shared taxonomy. Of 73,930 documents,
40,217 map at least one source component to `other`; richer component taxonomy should be a
separate reviewed decision.

Summary and NHTSA component fields are present for all documents. Only 1,168 documents
(1.5799%) have structured manufacturer system/subsystem data, and 2,143 (2.8987%) have a
campaign/software identifier. The deprecated replacement-bulletin field is blank throughout
this archive. Two out-of-range manufacturer communication dates are preserved with flags.

## Matching, aggregation, and outputs

Matching uses exact normalized make/model/model-year identity, then an inspectable explicit
alias table. The validated alias table is empty. There is no fuzzy, substring, regex,
edit-distance, embedding, or LLM matching. Applicability statuses are `ELIGIBLE` and
`INSUFFICIENT_VEHICLE_IDENTITY`; match statuses are `EXACT`, `EXPLICIT_ALIAS`, `AMBIGUOUS`,
`NO_MATCH`, and `INSUFFICIENT_VEHICLE_IDENTITY`.

Outputs under ignored `data/processed/manufacturer_communications/` contain canonical
communications, deduplicated vehicle applicability, applicability matches, one evidence row
per complaint cohort, unmatched diagnostics, cross-source coverage, and checksummed
provenance. Schemas and ordering are fixed. Existing targets are never overwritten.

```console
python -m howreliable.data.manufacturer_communications retrieve \
  --raw-directory data/raw/nhtsa/manufacturer_communications/2026-09-13
python -m howreliable.data.manufacturer_communications ingest \
  --manifest data/raw/nhtsa/manufacturer_communications/2026-09-13/acquisition-manifest.json \
  --communications data/processed/manufacturer_communications/communications.jsonl \
  --applicability data/processed/manufacturer_communications/applicability.jsonl \
  --provenance data/processed/manufacturer_communications/ingestion.provenance.json
```

The match CLI additionally receives the Phase 2C complaint cohorts and Phase 2D match table.
It does not mutate either input.

## Full validation result

The TSV produced 73,930 communications and 1,691,120 distinct applicability records, of
which 22,945 have unknown model year. Exactly 917,292 applications match complaint cohorts;
750,883 do not, and 22,945 have insufficient year identity. No alias or ambiguous real match
occurred.

Manufacturer evidence exists for 9,234 of 10,670 complaint cohorts (86.5417%) and 406,918 of
412,866 complaint events (98.5593%). Those cohorts map 55,683 unique NHTSA communications
through 917,292 applications. Unique communications per matched cohort have minimum 1,
median 36, 75th percentile 103, 95th percentile 464, and maximum 1,295. These counts measure
documents, not defects, failures, repairs, risk, or reliability.

Unmatched cohorts comprise 280 with a make absent from eligible applications, 420 with a
model absent for a present make, and 736 absent make/model/year combinations. Common examples
include Ford, Honda, Forest River, Thor Motor Coach, Yamaha, Keystone, Chevrolet, Fleetwood,
Kawasaki, and Mercury. Source naming examples include Silverado/F-Series variants, historical
Ram branding, RV names, motorcycle names, and model suffixes; none are guessed into matches.

Coverage is sparse before the mid-1990s, exceeds roughly 80% of cohorts for most 2000-2024
model years, and is especially strong by complaint-event weight for common makes. Exact
year- and make-level counts remain in `matching.provenance.json`.

## Cross-source coverage

Every row remains a complaint cohort; the categories indicate coverage only:

| Coverage | Cohorts | Cohort % | Complaint events | Event % |
|---|---:|---:|---:|---:|
| Complaint only | 1,249 | 11.7057% | 4,461 | 1.0805% |
| Complaint + production | 187 | 1.7526% | 1,487 | 0.3602% |
| Complaint + communications | 4,646 | 43.5426% | 88,123 | 21.3442% |
| Complaint + production + communications | 4,588 | 42.9991% | 318,795 | 77.2151% |

## Limitations and checkpoint recommendation

- Communications document manufacturer action or guidance, not verified field failures.
- A communication can span products, years, and components; application counts are not
  document counts.
- Repeated manufacturer document IDs and unavailable supersession data prevent speculative
  revision collapsing.
- The received-range archive contains older model years and communication dates; these do
  not imply contemporaneous vehicle failures.
- Structured manufacturer component and campaign metadata are sparse.
- Summary text is preserved but deliberately not classified.
- Equipment applicability cannot be reliably identified because the flat schema has no
  product-type or reporting-manufacturer field.

Manufacturer communication counts are defensible future descriptive features only when
document identity, observation cutoff, application grain, type/component metadata, and
missingness are retained. They should not become targets or severity proxies. Recalls remain
necessary before Phase 3A because they provide a distinct, safety-regulatory action signal
that communications do not. The combined sources support a stronger future target-design
discussion, but Phase 2F/recall review should occur first. Phase 3A has not started.
