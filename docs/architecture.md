# Architecture

## Current implementation

The repository contains an installable Python package, reusable configuration and logging
foundations, and an immutable canonical vehicle domain model. Tests and static-analysis
configuration enforce these boundaries. No ingestion, reliability-event, data-processing,
ML, API, deployment, or UI component is implemented.

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
