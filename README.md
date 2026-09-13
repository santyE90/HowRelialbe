# HowReliable?

HowReliable? is the foundation of a cloud-based vehicle maintenance and reliability
intelligence platform. The long-term aim is to turn well-understood automotive data into
probabilistic repair or failure-risk estimates while providing hands-on experience with
machine learning, data science, and cloud/ML engineering.

The planned stack includes Python, Pandas, NumPy, scikit-learn, PyTorch, FastAPI, Docker,
AWS, GitHub Actions, and Terraform. These technologies will be introduced only when their
roadmap phase calls for them.

## Current status

Phases 0 through 2G now provide the repository foundation, canonical vehicle and reliability
event models, source-faithful NHTSA ingestion, reproducible full-corpus EDA, and a
deterministic evidence-preserving cleaning boundary, target-agnostic event and vehicle
cohort features, NHTSA production-exposure diagnostics, and official manufacturer-
communication evidence, official recall campaign evidence, and a deterministic four-source
cohort integration and dataset review. The project is ready for Phase 3A target design; there is no target
definition, training dataset, trained model, inference API, infrastructure, or user interface.
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
