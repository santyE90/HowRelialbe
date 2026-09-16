# Architecture

## Current implementation

The repository contains an installable Python package, reusable configuration and logging
foundations, immutable canonical vehicle and reliability-event models, source-specific NHTSA
ingestion and mapping, reproducible EDA, deterministic cleaning, target/feature generation,
traditional baselines, bounded neural training/evaluation, a typed API, a checksum-first
registry, and local/S3 artifact stores. Tests and static-analysis configuration enforce these
boundaries. No deployment or UI component is implemented.

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

### Recall boundary

Phase 2F treats NHTSA recalls as formal safety-defect/noncompliance actions, not
`ReliabilityEvent` instances. Exact NHTSA campaign identity remains separate from each
official vehicle application. Campaign-wide affected population is retained once and is
never summed or allocated across applications.

Only structured product, date, component, FMVSS, and advisory fields are interpreted.
Recall text remains preserved evidence. Exact normalized matching emits separate cohort and
four-source coverage diagnostics without changing Phase 2C–2E artifacts. See
[recalls](recalls.md).

### Multi-source integration boundary

Phase 2G consumes only validated Phase 2C–2F processed artifacts and verifies their schemas,
versions, and checksums. Phase 2C is the 10,670-row base, so no complaint cohort is dropped.
Unmatched source values are null while genuine zeros require a matched source. The integrated
artifact remains descriptive and human-readable; it contains no target, label, imputation,
encoding, split, or score.

Separate review diagnostics describe source support, complete-pair correlations, and what
could have been known at calendar cutoffs. They do not turn the whole-history artifact into
a training dataset. See [dataset review](dataset-review.md).

### Target-definition boundary

Phase 3A reads validated Phase 2C complaint events and Phase 2G cohort identities. It uses
complaint `report_date` to separate history from a complete future source window and emits a
separate target-only artifact for eligible cohorts. Target generation verifies input
versions, schemas, checksums, identity accounting, cutoff boundaries, and censoring before
writing; incomplete windows emit no labels.

The selected target is observed 12-month complaint activity at cohort grain. It is not a
repair, failure, health, safety, or reliability target. Whole-history integrated features
are not joined into the target artifact and remain prohibited for modeling until Phase 3B
rebuilds temporal features as of the cutoff. See [target definition](target-definition.md).

### Baseline-model boundary

Phase 3B reads the validated target plus Phase 2C complaint events, Phase 2E communication
documents/applications, and Phase 2F recall campaigns/applications. It verifies checksums and
rebuilds one separate feature row per eligible cohort using only evidence observable by
2022-12-31. It does not consume non-static Phase 2G integrated columns; production is omitted
because historical revision state cannot be recovered.

The deterministic split artifact contains identity and assignment only. Learned imputation,
missing indicators, scaling, and optional one-hot encoding live inside sklearn pipelines fit
on training rows. Serialized baseline models and their machine-readable metrics are ignored
research artifacts, not production deployments or a model registry. See
[baseline models](baseline-models.md).

### PyTorch data-pipeline boundary

Phase 3C verifies the exact Phase 3A/3B checksums and frozen cohort assignments, then fits a
small serializable preprocessor using training rows only. It emits a deterministic tensor
manifest, preprocessing parameters, and dataset metadata; canonical JSON Lines remain the
source of truth, so no duplicate tensor artifact is stored.

`HowReliableCohortDataset` holds precomputed CPU float32 tensors and separate targets.
Seeded training DataLoaders shuffle, while validation/test loaders preserve order. Cohort
identity and age/support/source metadata remain outside the model tensor. This boundary has
no neural architecture, loss, optimizer, training loop, prediction, or checkpoint. See
[PyTorch data pipeline](pytorch-data-pipeline.md).

### First-neural-network boundary

Phase 3D adds one readable `HowReliableMLP`, an explicit BCE-with-logits/AdamW training loop,
validation ROC-AUC early stopping, validation-only bounded candidate selection, metrics,
calibration, subgroup diagnostics, and a deterministic five-seed robustness check. Only the
selected primary-seed state is evaluated on test and persisted.

The checkpoint contains a `state_dict` and explicit contract metadata rather than a pickled
module. Epoch history and complete results are separate machine-readable, overwrite-protected,
Git-ignored artifacts. This is a single experiment, not a trainer framework, registry,
scheduler system, or Phase 3E implementation. See
[first neural network](first-neural-network.md).

### Training-infrastructure boundary

Phase 3E wraps the fixed Phase 3D configuration in deterministic content-addressed run
directories. Atomic artifacts separate immutable configuration, mutable status, epoch
history, resumable latest state, best validation checkpoint, and explicit split evaluation.
Model/optimizer/early-stop/RNG restoration supports exact epoch-boundary resume while strict
configuration and lineage checks reject incompatible state.

Training never evaluates TEST. A separate CLI operation must explicitly request TEST from a
completed best checkpoint. The layer is local and intentionally small: it contains no sweep,
trainer framework, registry, database, cloud execution, or distributed system. See
[training infrastructure](training-infrastructure.md).

### Evaluation boundary

Phase 3F writes deterministic row-aligned frozen prediction artifacts before evaluation. One
authoritative module validates alignment and computes aggregate metrics, paired bootstrap
uncertainty, calibration, fixed-threshold sensitivity, established subgroups, error patterns,
and model agreement. A separate handoff binds the preferred frozen forest and serialized
preprocessing to the evaluation/feature/target/split contracts. No training or explainability
occurs in this boundary. See [model evaluation](model-evaluation.md).

### Explainability boundary

Phase 4A validates the preferred-model handoff and decomposes frozen forest predictions into
exact tree-path probability contributions. It maps transformed inputs to evidence semantics,
aggregates by family/source, and cross-checks with validation permutation importance. It does
not train, score reliability, infer causes, or generate user-facing risk output. See
[model explainability](model-explainability.md).

### Presentation-contract boundary

Phase 4B validates the Phase 3F preferred-model and Phase 4A explanation contracts, performs
frozen inference for an existing Phase 3B cohort, and returns one immutable
`ComplaintActivityResult`. Identity, literal complaint-activity probability and binary label,
compact explanation, factual evidence, limitation flags, general limitation, and provenance
are serialized deterministically. Evaluation ground truth is optional and separate.

The ignored presentation artifacts include the JSON schema, six representative results, and
the exact Phase 5A handoff. This boundary does not train, recalibrate, tune thresholds, score
risk or confidence, recommend actions, ingest unseen vehicles, or implement HTTP/API code.
See [complaint-activity presentation](complaint-activity-presentation.md).

### Local API boundary

Phase 5A's application factory validates the Phase 4B handoff before constructing a service.
The service owns one deserialized frozen forest, one indexed immutable feature-row mapping,
and one explanation manifest for its lifetime. Thin synchronous GET routes expose health,
safe metadata, paginated supported identities, and the unchanged `ComplaintActivityResult`.

Importing the package performs no resource loading. Factory failure prevents service startup;
requests never trigger training, artifact reloading, fuzzy matching, or field reconstruction.
The boundary has no database, CORS default, authentication, Docker, or cloud deployment. See
[API documentation](api.md).

### Model-registry and loading boundary

Phase 5B places a strict portable manifest and `ArtifactStore` abstraction between local
files and `PredictionService`. The local store rejects unsafe references. `ModelRegistry`
requires an explicit bundle ID; `load_inference_bundle` checks the manifest and every artifact
before parsing or trusted joblib deserialization, validates cross-contract semantics, and
returns an immutable typed bundle.

The API now knows the selected bundle ID and bundle object, not individual filesystem paths
or checksum rules. Resources still load once during factory construction. There is no latest
alias, registry database, promotion workflow, or model change. See
[model registry](model-registry.md).

### S3 artifact-storage boundary

Phase 6A maps the same relative manifest keys through `S3ArtifactStore`. Configuration
explicitly chooses local or S3; S3 adds a private bucket and optional normalized prefix.
Downloaded bytes, not ETags, are SHA-256 checked through the shared checksum-first loader.
Startup fails closed and retains the validated typed bundle, so requests have no storage
dependency. Separate publication tooling validates locally, uploads artifacts before the
manifest/checksum marker, refuses conflicting bytes, and validates remotely. See
[AWS S3 artifact storage](aws-s3.md).

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

Implemented ingestion, cleaning, training/evaluation, registry, API, and S3 storage boundaries
already follow this separation; packaging and deployment remain planned.
Training, evaluation, and online inference will remain separable so they can be tested and
operated independently. Artifact metadata and data provenance connect stages rather than
hidden shared state. Concrete storage, service, and deployment designs will be selected in
their respective phases once requirements are known.
