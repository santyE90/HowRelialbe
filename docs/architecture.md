# Architecture

## Current implementation

The repository contains an installable Python package, reusable configuration and logging
foundations, an immutable canonical vehicle domain model, and a source-specific NHTSA ODI
complaint ingestion adapter. Tests and static-analysis configuration enforce these
boundaries. A separate canonical reliability-event model and conservative NHTSA mapper now
provide the source-to-domain boundary. No cleaning, EDA, feature-engineering, ML, API,
deployment, or UI component is implemented.

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

Each arrow represents a planned boundary, not current functionality. Data processing,
training, evaluation, and online inference will remain separable so they can be tested and
operated independently. Artifact metadata and data provenance will connect the stages rather
than hidden shared state. Concrete storage, service, and deployment designs will be selected
in their respective phases once requirements are known.
