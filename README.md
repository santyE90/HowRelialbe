# HowReliable?

HowReliable? is the foundation of a cloud-based vehicle maintenance and reliability
intelligence platform. The long-term aim is to turn well-understood automotive data into
probabilistic repair or failure-risk estimates while providing hands-on experience with
machine learning, data science, and cloud/ML engineering.

The planned stack includes Python, Pandas, NumPy, scikit-learn, PyTorch, FastAPI, Docker,
AWS, GitHub Actions, and Terraform. These technologies will be introduced only when their
roadmap phase calls for them.

## Current status

Phase 0 establishes packaging, configuration, logging, testing, and documentation. There is
currently no data pipeline, trained model, inference API, infrastructure, or user interface.
See the [roadmap](docs/roadmap.md) for planned work.

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

