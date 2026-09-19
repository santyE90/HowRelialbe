# HowReliable?

HowReliable? is the foundation of a cloud-based vehicle maintenance and reliability
intelligence platform. The long-term aim is to turn well-understood automotive data into
probabilistic repair or failure-risk estimates while providing hands-on experience with
machine learning, data science, and cloud/ML engineering.

The planned stack includes Python, Pandas, NumPy, scikit-learn, PyTorch, FastAPI, Docker,
AWS, GitHub Actions, and Terraform. These technologies will be introduced only when their
roadmap phase calls for them.

## Current status

Phases 0 through 7B now provide the repository foundation, canonical vehicle and reliability
event models, source-faithful NHTSA ingestion, reproducible full-corpus EDA, and a
deterministic evidence-preserving cleaning boundary, target-agnostic event and vehicle
cohort features, NHTSA production-exposure diagnostics, and official manufacturer-
communication evidence, official recall campaign evidence, and a deterministic four-source
cohort integration and dataset review, a leakage-safe cohort target definition, and
traditional baseline models on a separate as-of-cutoff feature matrix, a deterministic
PyTorch Dataset/DataLoader pipeline, a first small validation-selected PyTorch MLP, and
minimal reproducible/resumable training infrastructure and a local typed FastAPI boundary.
The API now has a locally validated S3 and ECS Fargate deployment and monitoring
implementation, but no live AWS deployment, cloud training, or user interface.

Phase 3F evaluates the frozen forest and MLP on aligned TEST predictions with deterministic
bootstrap uncertainty, calibration, threshold sensitivity, subgroups, errors, and agreement.
The random forest remains preferred; neither model is production-ready. See
[model evaluation](docs/model-evaluation.md).

Phase 4A explains the frozen forest with exact tree-path probability contributions and a
validation permutation cross-check. See [model explainability](docs/model-explainability.md).
Phase 4B packages that probability, binary target interpretation, compact explanation,
factual evidence, limitation flags, and lineage into the immutable
`complaint-activity-result-1.0` contract. It was reframed from the historically planned Risk
Scoring milestone because the target cannot support such a score. See
[complaint-activity presentation](docs/complaint-activity-presentation.md).
Phase 5A exposes that exact result for the 8,416 frozen cohorts through four read-only routes,
with fail-fast artifact validation and application-lifetime resource reuse. See the
[API documentation](docs/api.md).
Phase 5B moves artifact paths, checksums, compatibility validation, and trusted model loading
behind an explicit local registry and typed inference bundle. See the
[model registry](docs/model-registry.md).
Phase 6A adds explicit private-S3 storage and deterministic publication behind that unchanged
registry, with downloaded-byte SHA-256 validation and local/S3 inference equivalence. See
[AWS S3 artifact storage](docs/aws-s3.md).
Phase 6B packages the unchanged API as a non-root container and defines a minimal ECR, ECS
Fargate, ALB, IAM, and private-S3 deployment contract. The implementation is locally and
statically validated; no live AWS deployment has been executed. See
[AWS deployment](docs/aws-deployment.md).
Phase 6C adds structured JSON container logs, request correlation, ECS-to-CloudWatch log
routing, 14-day retention, and four provisional native infrastructure alarms. Local/static
validation is complete; CloudWatch was not validated against a live deployment. See
[monitoring](docs/monitoring.md).
Phase 7A adds a four-job GitHub Actions quality gate for Python quality, pytest, deterministic
contracts, and a non-published production Docker build. It needs no AWS credentials and does
not deploy. Local validation is complete; no live Actions run has been executed. See
[continuous integration](docs/ci.md).
Phase 7B adds a manual, OIDC-authenticated, rollback-capable GitHub Actions deployment
workflow for an immutable SHA-tagged ECR image and the existing ECS Fargate service. It does
not publish the model bundle or create infrastructure. Local/static validation is complete;
the 11.56 GB production image and genuine canonical-bundle container smoke passed locally,
but no live CD or AWS deployment has run. See [continuous deployment](docs/cd.md).
See the [roadmap](docs/roadmap.md), [cleaning rules](docs/cleaning-rules.md), and
[feature definitions](docs/feature-engineering.md) for details.

Phase 2D keeps production exposure separate from complaint events and features. Its exact
match covers 4,775 of 10,670 complaint cohorts and 77.58% of complaint events; unmatched
production is never treated as zero. See [exposure data](docs/exposure-data.md).

Phase 2E preserves 73,930 NHTSA communications separately from 1,691,120 vehicle
applications. Exact matching supplies manufacturer-side evidence to 9,234 complaint cohorts
without NLP, fuzzy matching, labels, or reliability interpretation. See
[manufacturer communications](docs/manufacturer-communications.md).

Phase 2F preserves 30,300 NHTSA recall campaigns separately from 286,158 vehicle
applications. Exact matching covers 8,529 complaint cohorts and 95.57% of complaint events;
campaign-wide population is never summed across application rows. See
[recalls](docs/recalls.md).

Phase 2G integrates all 10,670 complaint cohorts without imputation or source-absence zeros.
The review finds that the data can support cohort-level future complaint-activity target
design, but not major-repair probability or individual-vehicle risk. See the
[dataset review](docs/dataset-review.md).

Phase 3A selects `future_12m_complaint_activity`: whether an eligible cohort has at least one
accepted complaint report during 2023 after a 2022-12-31 cutoff. The ignored target artifact
is separate from features and represents reporting activity—not repair, failure, or
reliability. See the [target definition](docs/target-definition.md).

Phase 3B reconstructs 8,416 feature rows using only evidence observable by 2022-12-31,
freezes a stratified 70/15/15 cohort split, and compares trivial rules, logistic regression,
random forest, and histogram gradient boosting. The validation-selected random forest has
test ROC-AUC .8928 and PR-AUC .9173, but performance degrades for old and sparse cohorts.
See [baseline models](docs/baseline-models.md).

Phase 3D trains three bounded MLP candidates and selects `90 -> 64 -> 32 -> 1` using
validation metrics only. Its test ROC-AUC .8915 and PR-AUC .9171 do not improve upon the
random forest, and old/one-complaint cohorts remain weak. Neural complexity is therefore not
justified by predictive performance. See [first neural network](docs/first-neural-network.md).

## Reproduce Phase 3B baselines

With the validated Phase 2C, 2E, 2F, and 3A artifacts present, run each overwrite-protected
stage once:

```console
python -m howreliable.modeling features
python -m howreliable.modeling split
python -m howreliable.modeling run
```

The commands write ignored feature/split artifacts beneath `data/processed/modeling/` and
ignored sklearn pipelines/results beneath `artifacts/models/`. They do not read the leaky
Phase 2G whole-history feature columns and do not use PyTorch.

## Generate the PyTorch data pipeline

After the exact Phase 3B artifacts exist, generate the overwrite-protected Phase 3C
manifest, preprocessing parameters, and dataset metadata with:

```console
python -m howreliable.modeling.pytorch
```

The pipeline reuses the frozen Phase 3B split, fits preprocessing on training rows only, and
constructs float32 Dataset/DataLoader objects without training a neural model. See the
[PyTorch data pipeline](docs/pytorch-data-pipeline.md).

## Run the bounded Phase 3D experiment

After the Phase 3C artifacts exist, train the three approved candidates, perform the selected
configuration's five-seed validation robustness check, and persist the primary checkpoint:

```console
python -m howreliable.modeling.pytorch.train
```

The command refuses to overwrite `artifacts/models/pytorch/`. It selects without test data,
then evaluates only the frozen selected checkpoint on test.

## Use the Phase 3E training infrastructure

The Phase 3D reference configuration can now be trained, resumed, inspected, and deliberately
evaluated through separate commands:

```console
python -m howreliable.modeling.pytorch.training_cli train
python -m howreliable.modeling.pytorch.training_cli resume artifacts/training/runs/<run_id>
python -m howreliable.modeling.pytorch.training_cli evaluate artifacts/training/runs/<run_id>
python -m howreliable.modeling.pytorch.training_cli evaluate artifacts/training/runs/<run_id> --split test
python -m howreliable.modeling.pytorch.training_cli inspect artifacts/training/runs/<run_id>
```

Training uses only TRAIN/VALIDATION; TEST requires the explicit flag. See
[training infrastructure](docs/training-infrastructure.md).

## NHTSA complaint ingestion

To ingest an already downloaded official artifact, supply its actual acquisition timestamp
so provenance is not guessed:

```console
python -m howreliable.data.ingestion \
  --artifact data/raw/nhtsa/complaints/COMPLAINTS_RECEIVED_2020-2024.zip \
  --retrieved-at 2026-09-12T14:00:00Z \
  --output data/interim/nhtsa/complaints/complaints.jsonl
```

To retrieve the configured official 2020–2024 artifact and ingest it:

```console
python -m howreliable.data.ingestion --download
```

The command never overwrites an existing raw, structured, or provenance artifact. `--limit`
is available for bounded diagnostics; its provenance explicitly marks the output incomplete.
Downloaded data and generated outputs are ignored by Git. See
[data sources](docs/data-sources.md) for format and provenance details.

## Reproduce the EDA

After producing the complete interim JSON Lines artifact at the default data location,
install the project kernel and execute the notebook from the repository root:

```console
python -m ipykernel install --prefix .venv --name howreliable --display-name "HowReliable (.venv)"
jupyter-execute --inplace --timeout=900 notebooks/01_data_exploration.ipynb
```

The notebook analyzes the complete artifact without modifying it. Its factual results are
summarized in [EDA findings](docs/eda-findings.md).

## Produce the cleaned analytical artifact

After complete ingestion, run the deterministic Phase 2B cleaner:

```console
python -m howreliable.data.cleaning
```

It writes clean JSON Lines, explicit exclusions, and provenance beneath
`data/processed/nhtsa/complaints/`. It retains questionable values with named quality flags,
accounts for every input row, and refuses to overwrite existing outputs. Generated data is
ignored by Git. See [cleaning rules](docs/cleaning-rules.md) for schema and policy details.

## Produce target-agnostic feature tables

After Phase 2B cleaning, generate the event and broad vehicle-cohort tables with:

```console
python -m howreliable.data.features
```

Outputs are written beneath `data/processed/features/` with fixed schemas and versioned
provenance. They are whole-corpus descriptive tables, not targets, reliability rates, or a
leakage-safe training dataset. See [feature engineering](docs/feature-engineering.md).

## Local setup

Python 3.12 or newer is required. From the repository root:

```console
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Configuration is read from environment variables. Copy `.env.example` only as a reference;
the application deliberately does not load `.env` files implicitly. Set variables in your
shell or deployment environment when overriding defaults.

## Development checks

```console
python -m pytest
python -m ruff check .
python -m mypy
python -c "import howreliable; print(howreliable.__version__)"
```

## Run the local API

With all validated ignored artifacts available from the repository root:

```console
uvicorn howreliable.api.app:create_app --factory
```

OpenAPI is available at `/openapi.json`, Swagger UI at `/docs`, and ReDoc at `/redoc`.

Validate the explicitly selected local inference bundle with:

```console
python -m howreliable.modeling.registry validate howreliable-rf-2022-cutoff-v1
```

To use S3, explicitly set `HOWRELIABLE_ARTIFACT_BACKEND=s3` plus bucket/prefix/region and
the bundle ID shown in `.env.example`. Publishing and remote validation are separate tools:

```console
python -m howreliable.cloud.s3 publish-bundle howreliable-rf-2022-cutoff-v1 --bucket <bucket> --prefix <prefix>
python -m howreliable.cloud.s3 validate-bundle howreliable-rf-2022-cutoff-v1 --bucket <bucket> --prefix <prefix>
```

Build the Phase 6B image with its explicit tag where Docker is available:

```console
docker build --pull --tag howreliable-api:1.0.0 .
```

The image contains application code and runtime dependencies only. The frozen bundle is
loaded from private S3 at startup in Fargate. Deployment remains a documented manual process;
see the deployment guide before substituting AWS resource placeholders.
