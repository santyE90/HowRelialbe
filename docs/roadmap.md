# Roadmap

Status markers: **complete** means implemented and validated; **planned** means no
implementation exists yet.

- **Phase 0 — Foundation: complete** — packaging, dependency configuration,
  centralized settings and logging, tests, and initial documentation.
- **Phase 1A — Vehicle Data Model: complete** — immutable canonical vehicle
  representation, validation, normalization, serialization, and stable identities.
- **Phase 1B — Structured Data Ingestion: complete** — official NHTSA ODI
  complaint retrieval, immutable raw artifacts, source-faithful parsing, deterministic JSON
  Lines output, and provenance.
- **Phase 1C — Reliability Event Schema: complete** — immutable canonical
  events, cross-source taxonomies, source traceability, and conservative NHTSA complaint
  mapping.
- **Phase 2A — Exploratory Data Analysis: complete** — reproducible full-corpus
  pandas/NumPy analysis, mapping diagnostics, data-quality visualizations, and documented
  findings without dataset mutation.
- **Phase 2B — Data Cleaning: complete** — deterministic clean JSON Lines,
  evidence-preserving quality flags, explicit exclusions and conservation accounting,
  versioned provenance, and full-corpus validation.
- **Phase 2C — Feature Engineering: complete** — target-agnostic event and broad
  vehicle-cohort tables, stable schemas, versioned provenance, and full-corpus accounting.
- **Phase 2D — NHTSA EWR Production Data Ingestion & Cohort Matching: complete** — official
  public production snapshots, immutable provenance, cumulative-safe aggregation, and
  conservative full-cohort exposure matching diagnostics.
- **Phase 2E — Manufacturer Communications / TSB Ingestion & Cohort Matching: complete** —
  official rich-TSV ingestion, compact-view overlap audit, document/applicability identity,
  structured-only classification, conservative cohort matching, and cross-source coverage.
- **Phase 2F — NHTSA Recall Ingestion & Cohort Matching: complete** — official complete flat
  corpus ingestion, campaign/applicability identity, product-aware filtering, conservative
  cohort matching, and four-source coverage diagnostics.
- **Phase 2G — Multi-Source Integration & Dataset Review: complete** — deterministic
  10,670-row four-source cohort artifact, explicit missingness, cross-source diagnostics,
  temporal feasibility analysis, and evidence-based Phase 3A direction.
- **Phase 3A — Define ML Target: complete** — versioned cohort-level future complaint-
  activity targets, explicit eligibility/censoring, full cutoff/horizon diagnostics, leakage
  audit, and a selected 2022 year-end/12-month/at-least-one-report target.
- **Phase 3B — Baseline ML Models: complete** — leakage-safe cutoff reconstruction,
  frozen stratified split, trivial rules, traditional sklearn models, family/identity
  ablations, persisted artifacts, and age/support/source diagnostics.
- **Current project state — BASELINE MODELS COMPLETE.** Phase 3C is the next checkpoint,
  but is justified only as a controlled comparison against the frozen random-forest baseline;
  sparse and old-cohort behavior must improve rather than being hidden by aggregate metrics.
- **Phase 3C — PyTorch Dataset Pipeline: planned**
- **Phase 3D — First Neural Network: planned**
- **Phase 3E — Training Infrastructure: planned**
- **Phase 3F — Evaluation: planned**
- **Phase 4A — Explainability: planned**
- **Phase 4B — Risk Scoring: planned**
- **Phase 5A — FastAPI Inference Service: planned**
- **Phase 5B — Model Registry / Loading: planned**
- **Phase 6A — AWS S3: planned**
- **Phase 6B — AWS Deployment: planned**
- **Phase 6C — Monitoring: planned**
- **Phase 7A — CI: planned**
- **Phase 7B — CD: planned**
- **Phase 7C — Terraform: planned**
- **Phase 8A — Owner Report NLP: planned**
- **Phase 8B — Source Normalization: planned**
- **Phase 8C — NLP Integration: planned**
- **Phase 9 — Component-Level Prediction: planned**
- **Phase 10 — Minimal Frontend: planned**
- **Phase 11 — Production Readiness: planned**
