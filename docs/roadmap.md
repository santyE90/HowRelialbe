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
- **Phase 3B checkpoint decision.** Phase 3C was justified only as a controlled comparison
  against the frozen random-forest baseline; sparse and old-cohort behavior must improve
  rather than being hidden by aggregate metrics.
- **Phase 3C — PyTorch Dataset Pipeline: complete** — exact Phase 3B input validation,
  training-only preprocessing, deterministic feature manifest, float32 Dataset objects,
  portable seeded DataLoaders, and reproducible metadata without model training.
- **Phase 3D — First Neural Network: complete** — bounded small-MLP comparison,
  validation-only selection, early stopping, deterministic checkpoint/history/results,
  five-seed robustness, calibration, and subgroup diagnostics. The selected MLP did not
  meaningfully improve upon the random forest.
- **Phase 3E — Training Infrastructure: complete** — immutable validated configuration,
  lineage-derived run IDs, atomic run artifacts, exact epoch-boundary resume, explicit
  train/evaluate separation, reproducibility manifests, and automatic forest comparison.
- **Phase 3F — Evaluation: complete** — aligned frozen prediction artifacts, deterministic
  aggregate and paired bootstrap intervals, calibration, fixed-threshold sensitivity,
  subgroup uncertainty, error/agreement analysis, and preferred-model handoff.
- **Phase 4A — Explainability: complete** — deterministic feature mapping, exact local
  tree-path probability contributions, global family/source summaries, validation permutation
  cross-check, representative explanations, reconstruction and terminology guards.
- **Phase 4B — Complaint-Activity Presentation and Decision Contract: complete** — immutable
  versioned result schema, literal probability/classification labels, compact Phase 4A
  explanations, factual evidence profiles, evaluated limitation flags, deterministic
  serialization, representative results, and a constrained Phase 5A handoff.
- **Phase 4B rename history.** The original planned milestone was **Phase 4B — Risk
  Scoring**. It was formally reframed after target/evaluation validation because observed
  future complaint activity cannot support a reliability or repair-risk score.
- **Phase 5A — FastAPI: complete** — explicit application factory, fail-fast frozen-artifact
  validation, application-lifetime resource loading, indexed deterministic cohort discovery,
  typed complaint-activity retrieval, structured errors, OpenAPI, and versioned API artifact.
- **Phase 5B — Model Registry / Loading: complete** — immutable portable manifest, explicit
  bundle selection, path-safe local artifact store, checksum-first loading, cross-artifact
  compatibility validation, typed read-only inference bundle, and unchanged API behavior.
- **Phase 6A — AWS S3: complete** — explicit local/S3 backend selection, safe relative-key
  object mapping, downloaded-byte checksum validation, deterministic fail-closed/idempotent
  publication, least-privilege reader policy, and validated local/S3 inference equivalence.
- **Phase 6B — AWS Deployment: complete** — non-root versioned container, ECR image contract,
  ECS Fargate task/service definitions, distinct least-privilege roles, ALB health/network
  design, and deterministic deployment contract. Implementation and local/static validation
  are complete; live AWS deployment was not executed.
- **Phase 6C — Monitoring: complete** — structured local/container logging, bounded request
  correlation, lifecycle/request events, ECS CloudWatch Logs routing with finite retention,
  four native-metric alarm specifications, and a deterministic monitoring contract.
  Implementation and local/static validation are complete; live CloudWatch validation was
  not executed.
- **Phase 7A — CI: complete** — one least-privilege GitHub Actions workflow with separate
  quality, test, deterministic-contract, and non-publishing Docker jobs; explicit clean-runner
  versus full-artifact test handling; and a versioned CI contract. Local implementation and
  structural validation are complete; no live GitHub Actions run was executed.
- **Phase 7B — CD: complete** — manual trusted-SHA deployment workflow, GitHub OIDC,
  least-privilege deployment-role templates, build-once immutable ECR images, validated ECS
  task revisions, bounded stability/smoke, and failure-preserving rollback. Implementation
  and local/static validation are complete; live CD/ECR/ECS/smoke were not executed.
- **Current project state — CD IMPLEMENTATION COMPLETE.** Phase 7C is next; there is no claim
  that GitHub Actions CD or a live AWS deployment has run.
- **Phase 7C — Terraform: planned**
- **Phase 8A — Owner Report NLP: planned**
- **Phase 8B — Source Normalization: planned**
- **Phase 8C — NLP Integration: planned**
- **Phase 9 — Component-Level Prediction: planned**
- **Phase 10 — Minimal Frontend: planned**
- **Phase 11 — Production Readiness: planned**
