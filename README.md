# HowReliable?

HowReliable? is the foundation of a cloud-based vehicle maintenance and reliability
intelligence platform. The long-term aim is to turn well-understood automotive data into
probabilistic repair or failure-risk estimates while providing hands-on experience with
machine learning, data science, and cloud/ML engineering.

The planned stack includes Python, Pandas, NumPy, scikit-learn, PyTorch, FastAPI, Docker,
AWS, GitHub Actions, and Terraform. These technologies will be introduced only when their
roadmap phase calls for them.

## Current status

Phase 0 established the repository foundation, and Phase 1A added the canonical vehicle
domain model. Phase 1B adds reproducible ingestion of the official NHTSA ODI Vehicle Owner
Complaints flat file. There is currently no reliability-event model, cleaning, feature
engineering, trained model, inference API, infrastructure, or user interface. See the
[roadmap](docs/roadmap.md) for planned work.

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
