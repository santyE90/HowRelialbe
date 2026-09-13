# Architecture

## Current implementation

The repository contains an installable Python package, reusable configuration and logging
foundations, immutable canonical vehicle and reliability-event models, source-specific NHTSA
ingestion and mapping, reproducible EDA, deterministic cleaning, and target-agnostic feature
engineering. Tests and static-analysis configuration enforce these boundaries. No target
definition, training dataset, ML model, API, deployment, or UI component is implemented.

### Vehicle identity decisions

`Vehicle` requires make, model, year, transmission, and drivetrain. Generation, trim, and
engine descriptor are optional and use `None` for missing information; empty strings are not
missing values. Descriptive text uses Unicode NFKC normalization, collapsed whitespace, and
case folding. This removes irrelevant representation differences without fuzzy matching.

Transmission and drivetrain use small explicit taxonomies. Documented common aliases map to
canonical enum values. `other` and `unknown` must be explicit; unsupported strings fail
validation instead of being guessed. Model years use the static inclusive range 1886–2100,
which rejects obvious errors without depending on the date when validation runs.

Broad identity consists of make, model, year, and generation. Configuration identity adds
trim, engine, transmission, and drivetrain. Each has a human-inspectable, sorted canonical
JSON key and a SHA-256 identifier derived from that key. IDs are stable across processes and
do not use Python's randomized object hash.

### Structured ingestion boundary

The NHTSA adapter isolates HTTPS retrieval, raw-artifact checksums, safe ZIP handling,
51-column flat-file parsing, source-record representation, JSON Lines output, and provenance.
Downloaded ZIP files are immutable raw inputs under `data/raw/nhtsa/complaints/`. Structured
records and provenance sidecars belong under `data/interim/nhtsa/complaints/`; neither may
replace raw data.

All source columns remain strings, with empty columns represented as `null`. Dates, numbers,
codes, narratives, and questionable values are not cleaned or interpreted during ingestion.
The output uses UTF-8 JSON Lines in official column order, making it streamable and directly
readable by Pandas in a later phase. Each run records its source URL, requested range,
timestamps, filename, SHA-256 checksum, adapter/schema version, record count, and whether the
artifact was processed completely.

`NhtsaComplaintRecord` remains separate from `Vehicle`. A narrow mapping function constructs
a canonical vehicle only when NHTSA explicitly supplies every Phase 1A required field; it
never invents generation, trim, engine, transmission, or drivetrain.

### Reliability event boundary

```text
immutable NHTSA artifact
  -> NhtsaComplaintRecord (all 51 source fields)
  -> explicit NHTSA mapper
  -> ReliabilityEvent (canonical cross-source schema)
```

The mapper parses only structurally necessary mileage, dates, safety indicators, source
identifiers, and basic vehicle identity. It conservatively maps the source component and
derives evidence severity. Raw records are neither modified nor replaced. Events retain
minimal source references and optional artifact checksum context while the complete evidence
remains in the raw/interim source layer. Mapping failures are explicit and categorized.

### Exploratory analysis boundary

Phase 2A reads the immutable Phase 1B JSON Lines artifact into pandas and uses NumPy-backed
diagnostic arrays plus the existing Phase 1C mapper. Analysis-only masks, parsed views,
counts, and plots remain inside the reproducible notebook. No notebook result is written back
to raw or interim source data, and no exploratory derivation is promoted to cleaning or
feature-engineering code.

### Cleaning boundary

Phase 2B streams the validated Phase 1B JSON Lines artifact through the Phase 1C mapper and
emits fixed-schema clean records under `data/processed/`. Original make/model labels,
component labels, source IDs, dates, mileage, narratives, and provenance remain traceable.
Canonical/normalized values are added without replacing the originals. Deterministic quality
flags expose EDA-backed anomalies without correction, imputation, or row deletion.

Rows outside vehicle scope or unable to form a canonical event are written to an explicit
exclusion artifact. Input conservation is checked against source provenance. Repeated ODINO
component rows remain separate, outputs never overwrite source or existing processed data,
and cleaning/mapping versions plus count summaries are recorded in a provenance sidecar.
Feature and label derivation remains outside this boundary.

### Feature-engineering boundary

Phase 2C reads only the Phase 2B clean artifact. It emits one fixed-schema table at event grain
and another at normalized make/model/model-year cohort grain. Event rows retain traceability
and derive calendar, nonnegative vehicle-age, evidence, mileage-coverage, and quality-status
values. Cohort rows aggregate stable canonical components, severity evidence, mileage
coverage, report-delay summaries, and quality composition. Repeated ODINO component events
remain represented.

Whole-corpus cohort aggregates are descriptive and may contain future information relative
to a later prediction cutoff. They are explicitly not training-ready. Target construction,
temporal cutoffs, splits, imputation, categorical encoding, scaling, and other model-specific
preprocessing remain separate future boundaries.

### Production-exposure boundary

Phase 2D independently acquires exact JSON responses from NHTSA's public EWR API. Immutable
raw responses and a checksummed manifest feed source-faithful production rows, immutable
canonical exposure records, latest-filing configuration selection, and broad production
cohorts. Cumulative quarterly filings are never summed. A separate diagnostic joins those
cohorts to Phase 2C identities without mutating either Phase 2C artifact.

Production exposure is not a `ReliabilityEvent`. Matching permits exact normalized identity
and inspectable explicit aliases only; the validated alias table is empty. Missing and
ambiguous exposure remain explicit nulls. See [exposure data](exposure-data.md).

### Manufacturer-communication boundary

Phase 2E treats NHTSA communications as documentary evidence, not `ReliabilityEvent`
instances. The richer headerless TSV is canonical; the compact CSV is audited for overlap
and never blindly unioned. Stable NHTSA communication identity remains separate from each
make/model/model-year applicability identity, preventing component and model-year expansion
from multiplying unique document counts.

Structured communication type and source component fields may be mapped exactly. Summary
text is preserved but never classified. A separate diagnostic matches applications to Phase
2C cohorts and combines only coverage status with Phase 2D. It does not alter complaint,
feature, or production artifacts. See [manufacturer communications](manufacturer-communications.md).

## Planned system

The intended high-level flow is:

```text
raw automotive data
  -> cleaning
  -> feature engineering
  -> ML training
  -> evaluation
  -> versioned model artifact
  -> FastAPI inference
  -> Docker packaging
  -> AWS deployment
  -> minimal UI (later)
```

Implemented ingestion and cleaning boundaries already follow this separation; later arrows
remain planned. Training, evaluation, and online inference will remain separable so they can
be tested and operated independently. Artifact metadata and data provenance connect stages
rather than hidden shared state. Concrete storage, service, and deployment designs will be
selected in their respective phases once requirements are known.
