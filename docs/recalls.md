# NHTSA recall data

## Purpose and official source

Phase 2F adds official NHTSA safety-defect and noncompliance campaigns as a distinct
regulatory-action source. A recall is not a complaint, general failure, repair record, or
proof that every potentially affected unit failed.

The canonical snapshot uses both complete flat-file partitions on the
[NHTSA datasets and APIs page](https://www.nhtsa.gov/nhtsa-datasets-and-apis):
`FLAT_RCL_PRE_2010.zip`, `FLAT_RCL_POST_2010.zip`, and the official `RCL.txt` dictionary.
NHTSA describes the dataset as daily and 1949-present. The dictionary describes campaigns
since 1967; source rows contain model years from 1949. Observed `RCDATE` values show the
partition boundary: PRE ends 2009-12-31 and POST begins 2010-01-01.

The inspected `RCL_FROM_YYYY_YYYY.zip` files are six-column recall-document indexes
(`NHTSA ID`, document name, make, model, model year, summary), not equivalent campaign flat
files. Their IDs are bounded by campaign-number year, and campaigns repeat for documents.
They omit most canonical fields and are not unioned. The current API was also inspected: it
is a vehicle/campaign lookup view and omits flat-file lineage fields, so it is not used for
reproducible bulk acquisition.

## Actual schema and parsing

Each complete archive contains one headerless, Latin-1, tab-delimited text member with 29
fields. Literal quotes are data, not quoting syntax; quote handling is disabled to avoid
merging physical records. Empty fields become explicit nulls and nonempty text is unchanged.

The fields, in official order, are `RECORD_ID`, `CAMPNO`, `MAKETXT`, `MODELTXT`, `YEARTXT`,
`MFGCAMPNO`, `COMPNAME`, `MFGNAME`, `BGMAN`, `ENDMAN`, `RCLTYPECD`, `POTAFF`, `ODATE`,
`INFLUENCED_BY`, `MFGTXT`, `RCDATE`, `DATEA`, `RPNO`, `FMVSS`, `DESC_DEFECT`,
`CONEQUENCE_DEFECT` (official spelling), `CORRECTIVE_ACTION`, `NOTES`, `RCL_CMPT_ID`,
`MFR_COMP_NAME`, `MFR_COMP_DESC`, `MFR_COMP_PTNO`, `DO_NOT_DRIVE`, and `PARK_OUTSIDE`.

`NhtsaRecallRecord` is immutable and source-faithful. `RECORD_ID` uniquely identifies a
source row. Official campaign `21V00J000` demonstrates why `CAMPNO` validation follows the
dictionary's `CHAR(12)` semantics instead of assuming an all-numeric serial suffix.

## Campaign and applicability semantics

The official `CAMPNO` is namespaced as `nhtsa_recall_<CAMPNO>` and is the campaign identity.
Manufacturer campaign numbers are retained but never deduplicate records: 555 nonblank
manufacturer numbers are reused by multiple NHTSA campaigns.

`CanonicalRecallCampaign` aggregates only by exact NHTSA campaign number. It retains all
observed structured types, manufacturer campaign numbers, manufacturers, report,
owner-notification and creation dates, initiators, regulation/FMVSS values, population,
components, defect/consequence/remedy/notes text, advisories, artifacts, row count, and
quality flags. Multiple distinct values remain sorted tuples. Similar text never establishes
campaign equality, revision, or supersession.

`RecallApplicability` represents each official vehicle row separately. Its SHA-256 identity
uses campaign number, official `RECORD_ID`, and original make/model/year. It preserves
original and normalized vehicle identity, product and component fields, manufacturing dates,
component identifiers, and source artifact. Thus 30,300 campaigns remain distinct from
286,158 vehicle rows; application rows never inflate unique campaign counts.

## Product, dates, population, component, and text policies

Only structured type `V` rows can match vehicle complaint cohorts. Equipment (`E`), tire
(`T`), child-restraint (`C`), and legacy/other (`I`/`X`) rows remain counted in campaign
provenance but are excluded from vehicle applicability. Of the vehicle rows, 581 have
unknown/unsupported year and receive `INSUFFICIENT_VEHICLE_IDENTITY`.

Date meanings remain distinct: `RCDATE` is Part 573 report received, `ODATE` owner
notification, `DATEA` record creation, and `BGMAN`/`ENDMAN` manufacturing bounds. Raw values
remain available and valid values receive ISO representations. No target window is derived.

`POTAFF` is campaign-wide and repeated across application rows. All campaigns in this
snapshot have one internally consistent value. It is retained once, never summed and never
divided across cohorts. If values conflict, the adapter preserves all values, makes the
single value null, and raises a quality flag.

Original component labels are retained. The existing exact-root component mapper is reused
without taxonomy expansion. Defect, consequence, remedy, and notes text is preserved but
never used for NLP, keywords, severity, sentiment, embeddings, or LLM classification.

## Matching, outputs, and provenance

Matching uses Phase 2C's normalized make + model + model year grain: Unicode NFKC,
trimmed/collapsed whitespace, and case folding. The explicit alias table is empty. No fuzzy,
substring, edit-distance, family-name, embedding, or free-text matching is permitted.

Cohort diagnostics keep unique campaigns separate from application rows and include dates,
structured type/component counts, FMVSS metadata, known campaign population, and remedy
coverage. These are not failure, repair, risk, severity, or reliability counts.

Ignored outputs under `data/processed/recalls/` are `campaigns.jsonl`,
`applicability.jsonl`, `applicability-matches.jsonl`, `cohort-recalls.jsonl`,
`unmatched-cohorts.jsonl`, `cross-source-coverage.jsonl`, and two provenance JSON files.
Raw snapshots live under `data/raw/nhtsa/recalls/<date>/`. Provenance records official URLs,
retrieval UTC, SHA-256, archive members, dictionary/schema and adapter versions, conservation,
product and metadata coverage, duplicate behavior, schemas, and output checksums. Retrieval
and output creation refuse overwrite.

```console
python -m howreliable.data.recalls retrieve --raw-directory data/raw/nhtsa/recalls/2026-09-13
python -m howreliable.data.recalls ingest \
  --manifest data/raw/nhtsa/recalls/2026-09-13/acquisition-manifest.json \
  --campaigns data/processed/recalls/campaigns.jsonl \
  --applicability data/processed/recalls/applicability.jsonl \
  --provenance data/processed/recalls/ingestion.provenance.json
```

## Validation and limitations

The snapshot parsed all 327,288 rows: 286,158 vehicle, 17,127 equipment, 22,306 tire,
1,161 child-restraint, and 536 legacy/other. It contains 30,300 campaigns and 12,573 distinct
nonblank manufacturer campaign numbers. Report dates span 1966-01-19 through 2026-09-09;
vehicle model years span 1949 through 2027.

The population field is present and internally consistent for all campaigns: 30,250 values
are positive and 50 are zero. Defect text is present for 27,899 campaigns (92.0759%),
consequence text for 25,409 (83.8581%), remedy text for 27,911 (92.1155%), and structured
FMVSS metadata for 6,057. Zero population is preserved as an official value and is not
reinterpreted as missing.

Exact matching covers 8,529 of 10,670 cohorts (79.9344%) and 394,575 of 412,866 events
(95.5697%). Those matches use 9,155 campaigns through 105,549 application rows. Unique
campaigns per matched cohort have minimum 1, median 3, p75 5, p95 11, and maximum 42; 1,893
cohorts have one recall. No alias or ambiguous match occurred.

Recall and communication coverage overlap for 7,649 cohorts. Recalls cover 880 cohorts not
covered by communications; 1,585 communication-covered cohorts lack an exact recall match.
Recalls therefore provide genuinely distinct structured safety/noncompliance evidence and
should remain a future temporally bounded feature family.

Four-source complaint-cohort coverage is:

| Coverage category | Cohorts | Cohort % | Complaint events | Event % |
|---|---:|---:|---:|---:|
| Complaints only | 525 | 4.9203% | 1,824 | 0.4418% |
| Complaints + production | 31 | 0.2905% | 153 | 0.0371% |
| Complaints + communications | 943 | 8.8379% | 5,864 | 1.4203% |
| Complaints + recalls | 724 | 6.7854% | 2,637 | 0.6387% |
| Complaints + production + communications | 642 | 6.0169% | 10,450 | 2.5311% |
| Complaints + production + recalls | 156 | 1.4620% | 1,334 | 0.3231% |
| Complaints + communications + recalls | 3,703 | 34.7048% | 82,259 | 19.9239% |
| All four sources | 3,946 | 36.9822% | 308,345 | 74.6840% |

Limitations include naming mismatches, sparse older coverage, broad `OTHER` components,
unknown revision/supersession semantics, and inability to allocate campaign-wide population
to a cohort. No matched recall is not proof of no defect. Whole-history counts are temporally
leaky: future feature generation must exclude campaigns after each prediction cutoff. Phase
2F creates no temporal slices, target, integrated matrix, or model.

Phase 2G subsequently integrates cohort-level recall diagnostics without allocating affected
population. Unmatched recall counts become null, while zero structured subtype counts remain
valid only for matched recall cohorts. Historical feasibility uses the Part 573 report-
received date. See [dataset review](dataset-review.md).
