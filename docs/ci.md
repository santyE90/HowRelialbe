# Phase 7A continuous integration

Phase 7A implements quality-gate contract `howreliable-ci-1.0` in
`.github/workflows/ci.yml`. It automates repository validation but performs no publication,
deployment, AWS authentication, or infrastructure mutation. Local structural validation is
complete; a live GitHub Actions run has not been executed.

## Triggers, permissions, and environment

CI runs for pull requests, pushes to the repository's current primary branch `main`, and
manual `workflow_dispatch` requests. Concurrency cancels an obsolete run for the same ref.
The workflow grants only `contents: read`; it grants no OIDC, package, or deployment write
permission and references no secrets.

Every Python job uses Ubuntu and CPython 3.12.11, matching the container base image and the
project's Python 3.12 requirement. `actions/setup-python` uses its pip cache keyed by
`pyproject.toml`. Installation follows the repository contract:

```console
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Official `actions/checkout@v4` and `actions/setup-python@v5` are the only actions. Jobs have
25–40 minute timeouts and do not use a version/OS matrix.

## Jobs and failure interpretation

The workflow has four independent jobs:

- `quality`: Ruff lint, strict mypy, `pip check`, package import/version,
  and a no-dependency wheel build. A failure means source, typing, dependency, or packaging
  validation failed.
- `tests`: runs `python -m pytest` without parallel execution. A failure means a
  repository-contained behavioral test failed.
- `contracts`: parses the workflow, statically validates ECS/IAM/CloudWatch JSON, and
  regenerates the S3, deployment, monitoring, and CI contracts at their canonical explicit
  timestamps. A failure means a tracked configuration or deterministic contract drifted.
- `docker`: builds `howreliable-api:ci-${GITHUB_SHA}`, inspects non-root user `10001:10001`,
  exposed port 8000, healthcheck, and command, then checks prohibited paths and imports the
  installed application. It never pushes the image.

Local equivalents are:

```console
python -m pip install -e '.[dev]'
python -m ruff check .
python -m mypy
python -m pip check
python -c "import howreliable; print(howreliable.__version__)"
python -m pip wheel --no-deps --wheel-dir dist .
python -m pytest
python -m howreliable.ci.contract validate --root .
docker build --tag howreliable-api:ci-local .
```

## Clean-checkout portability and fixtures

The audit found four classes:

- A: most unit/schema tests and all new CI/static-definition checks are fully tracked.
- B: ingestion/modeling tests construct small deterministic fixtures under pytest temporary
  directories.
- C: API, registry/S3 equivalence, deployment frozen-result, explainability, presentation,
  and some monitoring regressions consume the ignored canonical 8,416-cohort bundle.
- D: Docker execution requires Docker; live S3/ECS/ALB/CloudWatch validation remains outside
  automated tests.

Only `data/README.md` is tracked under `data/`/`artifacts/`; the local scientific products
include multi-gigabyte raw/intermediate data and must not be committed or downloaded in
ordinary CI. `tests/conftest.py` explicitly marks the seven C-class modules
`full_artifacts`. Plain `pytest` runs them when the canonical manifest is provisioned and
reports explicit skips when it is absent. Tests are not deleted, weakened, or silently
filtered. The full-artifact gate remains the authoritative API, exact frozen probability and
classification, explanation reconstruction, limitation, registry, and local/S3 equivalence
regression; local Phase 7A validation ran it successfully.

The clean-runner gate still checks scientific transformation behavior using tracked code and
small generated fixtures. It never retrieves NHTSA corpora, accesses an S3 bucket, loads an
AWS profile, or requires ECR, ECS, ALB, or CloudWatch. This split is necessary until Phase 7B
defines an authorized immutable-artifact delivery mechanism.

## Deterministic contracts

`src/howreliable/ci/contract.py` provides a strict typed `CIContract`, atomic generation,
safe YAML parsing, workflow policy validation, AWS JSON validation, and deterministic
S3/deployment/monitoring regeneration. The ignored artifact is
`artifacts/ci/ci-contract.json`. It records Python, triggers, permissions, jobs and commands,
Docker build/no-push behavior, the no-credentials/no-live-AWS/no-deployment boundary,
regression categories, fixture policy, and generation UTC.

The registry/API source versions remain validated in a clean checkout. When the canonical
bundle is available, its manifest checksum and complete registry/API/inference semantics are
also validated. Broader registry/API behavior and negative-path coverage remains in the
explicitly marked full-artifact suite because those contracts cannot honestly be
reconstructed without the frozen model and 8,416-row inputs.

## Docker and container-smoke scope

The Docker job is the intended GitHub-hosted production-image build. The image-content
audit verifies there is no `/app/.env`, `.git`, `data`, `artifacts`, pytest cache, or
application-user AWS directory. The smoke imports `howreliable` and the FastAPI factory
module inside the image, validating packaging and the process import path without loading a
bundle.

On a clean GitHub runner it deliberately does not start a prediction-capable service or claim
`/health` success: the production task expects S3, and the ignored real bundle is unavailable
in a clean checkout. Faking S3 or fabricating a model would make that claim misleading. The
Dockerfile HEALTHCHECK is structurally inspected there. A separate local Phase 7B validation
mounted the genuine ignored bundle read-only and confirmed a healthy container, the required
model metadata, all 8,416 cohorts, one representative frozen prediction, and explanation
reconstruction error of approximately 5.6e-17. That local result is not a substitute for the
post-deployment S3-backed smoke.

## Regressions and cloud boundary

API routing/pagination/404/422/sanitization/request IDs, frozen prediction/classification,
explanation reconstruction, limitation flags, registry integrity, S3 equivalence, and
monitoring behavior remain in the full-artifact suite and run whenever the canonical bundle
exists. Repository-contained monitoring and contract validators require no CloudWatch.

CI has no AWS credential variables, OIDC assumption, AWS CLI installation, boto3 live call,
ECR login, image push, S3 publication, ECS update, or resource-creation step. Static AWS JSON
parsing is not deployment.

## Known limitations and Phase 7B handoff

No live GitHub Actions run has yet validated the hosted-runner path. A local Docker Engine
build produced and audited the production image, imported the package, and served the genuine
canonical bundle successfully. The resulting image was about 11.56 GB, reflecting the
existing runtime dependency set, including the Linux PyTorch wheel and CUDA-related transitive
packages. Image-size optimization is deferred. Clean GitHub runners explicitly skip the full-artifact
modules until a secure, immutable source for the canonical bundle exists; their CI container
smoke remains import/static only.

Phase 7B now consumes this non-deploying quality gate in its separate manual workflow. It
reruns deployment-critical validation before OIDC authentication, then handles image
publication, ECS revision/update, rollback, and smoke without weakening `ci.yml`. Model-bundle
publication remains separate. See [continuous deployment](cd.md).

Phase 7C adds its offline Terraform ownership/security validator to the existing `contracts`
job. CI does not install Terraform, contact AWS, plan, apply, or create infrastructure. Real
Terraform CLI and live-cloud validation remain explicit operator steps; see
[Terraform](terraform.md).
