# Phase 5A FastAPI boundary

Phase 5A exposes the validated frozen presentation contract through a deliberately small
HTTP application. API contract version `howreliable-api-1.0` is distinct from result contract
version `complaint-activity-result-1.0`. The service predicts accepted observed NHTSA
complaint activity for normalized make/model/model-year cohorts; it does not expand the
target or model semantics.

## Startup and resource lifecycle

`create_app()` is an explicit application factory. Importing `howreliable.api` defines code
only: it does not read feature data, deserialize the forest, or run inference. Factory
creation loads the explicit Phase 5B inference bundle, whose registry validates the Phase 4B
API handoff and all downstream model/evaluation/explanation/feature/target contracts. It loads
the forest once, builds an
8,416-entry cohort index once, and derives the transformed-feature manifest once. Requests
reuse those read-only resources.

The service requires the validated bundle documented in the Phase 5B registry, selected from
the local default or explicit Phase 6A S3 backend. A missing, malformed, inaccessible, or
checksum-mismatched artifact raises `ArtifactContractError` during factory creation; no
partially ready application serves predictions and S3 never falls back to local. Resources
load once at startup, so requests do not download or dynamically swap artifacts.

Phase 6B runs this same factory in one non-root container on port 8000. ECS and Docker health
checks call the existing `/health` route only after fail-closed bundle startup; no endpoint or
response schema changes were introduced. See [AWS deployment](aws-deployment.md).

Phase 6C adds `X-Request-ID` response correlation and operational logs without changing any
response body. A safe incoming ID (at most 64 restricted characters) is preserved; otherwise
a UUID is generated. Logs contain method, route template, status, duration, and request ID,
but never cohort IDs, payloads, probabilities, classifications, contributors, features,
headers, or ground truth. Successful `/health` logging is DEBUG. See
[monitoring](monitoring.md).

Run locally from the repository root after installing the project and restoring its validated
artifacts:

```console
uvicorn howreliable.api.app:create_app --factory
```

No CORS middleware is enabled by default. The API uses no accounts, authentication, cookies,
tracking, or personal information.

## Endpoints

| Method and path | Contract |
|---|---|
| `GET /health` | Cheap process liveness: `status=ok`, `service=howreliable` |
| `GET /api/v1/model` | Safe model, target, threshold, version, semantics, and limitation metadata |
| `GET /api/v1/cohorts` | Deterministically paginated frozen cohort identities |
| `GET /api/v1/cohorts/{cohort_id}` | Canonical `ComplaintActivityResult` |

`/health` does not perform inference. Because factory creation is fail-fast, a separate
readiness endpoint would duplicate application existence rather than express another state.

The cohort collection defaults to `limit=50, offset=0`; limit is constrained to 1–100 and
offset to zero or greater. Items contain only cohort ID, normalized make/model, and model
year. Ordering is normalized make, normalized model, model year, then cohort ID. This is
stable across pages. There is no fuzzy search, autocomplete, external lookup, or POST body
for arbitrary vehicles.

The cohort route calls `PredictionService.predict`, which calls the authoritative
`build_complaint_activity_result` with the already-loaded Phase 4B resources. It returns the
validated result directly and omits the optional `evaluation_context`; future observed target
values are never public inference inputs or outputs.

An unsupported exact cohort ID returns HTTP 404 with:

```json
{
  "error": {
    "code": "COHORT_NOT_FOUND",
    "message": "The requested cohort is not supported by the frozen model."
  }
}
```

Malformed pagination receives FastAPI's documented HTTP 422 validation response. Unexpected
request failures are logged server-side and return stable `INTERNAL_ERROR` text without
exception details. Internal paths, feature vectors, secrets, and large metadata documents are
not exposed.

Model metadata additionally exposes `howreliable-model-registry-1.0` and the explicit bundle
ID `howreliable-rf-2022-cutoff-v1`. The API remains backward-compatible with Phase 5A.

## Schema and documentation

All successful responses use typed Pydantic models. Cohort retrieval directly uses
`ComplaintActivityResult`; no HTTP-layer inference, threshold, explanation, limitation, or
provenance reconstruction exists. Built-in `/openapi.json`, `/docs`, and `/redoc` describe
the four endpoints, parameter constraints, response models, and complaint-activity semantics.

The service exposes no unsupported scoring bands, certainty metric, mechanical outcome, or
prescriptive recommendation. Historical/restriction documentation and the machine-readable
handoff list prohibited terminology so future consumers can enforce the same boundary.

## Artifacts, limitations, and Phase 5B handoff

The ignored `artifacts/api/api-contract.json` records API/result versions, route list,
preferred model identity/checksum, cohort grain/count, target, threshold, evaluation and
explainability lineage, Phase 4B handoff checksum, required limitation, prohibited language,
and UTC generation time. Generation is byte-identical for the same timestamp and inputs.

The API supports only the 8,416 frozen cohorts and current cutoff/window; it has no database,
dynamic promotion, arbitrary-vehicle feature construction, deployment, or availability
guarantee. Phase 5B supplies the typed registry described in
[model registry](model-registry.md), and Phase 6A supplies optional S3 storage behind it.
Neither changes `howreliable-api-1.0`, `complaint-activity-result-1.0`, the frozen model, or
the scientific restrictions. Phase 6B packages this boundary but does not claim a live AWS
service or production availability.
