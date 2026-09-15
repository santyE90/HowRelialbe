# Phase 5B local model registry and loading

Phase 5B formalizes the local artifact boundary behind the validated API. It is a small
filesystem manifest and checksum-first loader, not training, model selection, experiment
tracking, promotion, deployment, a database, or cloud storage.

## Registry and bundle identity

The registry contract is `howreliable-model-registry-1.0`. Its single approved explicit
bundle is `howreliable-rf-2022-cutoff-v1`. The ID identifies this exact frozen 2022-cutoff
random-forest bundle; it is never inferred from timestamps and is not an alias such as
“latest,” “best,” or “production.” Registry membership retains model status `PREFERRED` and
does not claim production readiness.

The canonical files are:

```text
artifacts/registry/bundles/howreliable-rf-2022-cutoff-v1/
  manifest.json
  manifest.sha256
```

`InferenceBundleManifest` is strict and immutable. It records registry/bundle/model identity,
model type/status, target version, history cutoff and future window, threshold, prediction
grain, embedded preprocessing contract, evaluation/explainability/result/API versions,
supported cohort count, mandatory limitation, generation time, and one unique reference for
each required artifact role.

Registered roles are `MODEL`, `FEATURE_DATA`, `TARGET_DATA`, `TARGET_PROVENANCE`,
`SPLIT_DATA`, `PREFERRED_MODEL_HANDOFF`, `EXPLANATION_FEATURE_MANIFEST`,
`EVALUATION_REPORT`, `EXPLAINABILITY_REPORT`, `PRESENTATION_CONTRACT`,
`PRESENTATION_HANDOFF`, and `API_CONTRACT`. Preprocessing is stored inside the exact
registered sklearn pipeline and is declared in the manifest rather than duplicated.

## Artifact store and path safety

`ArtifactStore` exposes only resolve, existence, checksum, byte-read, and prefix-list
operations. `LocalArtifactStore` implements them relative to an explicitly configured root.
Manifest locations use portable forward-slash relative keys. Absolute POSIX/Windows paths,
backslashes, `..` traversal, empty/dot segments, and resolved paths outside the root are
rejected. Moving the same relative layout under another root preserves resolution.

This interface is the Phase 6A storage seam. A future implementation may map the same logical
keys to object storage, but Phase 5B contains no S3 implementation, AWS SDK, download, or
network behavior.

## Checksum-first loading and trust

`ModelRegistry` discovers bundle directories but loads only a caller-supplied exact bundle
ID. The manifest sidecar digest is checked before manifest JSON is parsed. The bundle loader
then resolves and verifies every registered artifact checksum before parsing any component or
deserializing the model. Missing files, malformed or mismatched checksums, invalid JSON, and
unknown bundle IDs fail closed; there is no fallback.

The sklearn pipeline is a trusted project artifact. Digest validation proves that its bytes
match the approved manifest; it does not make hostile pickle/joblib data safe. The loader
never accepts an arbitrary user model and deserializes only after all bundle bytes pass their
checksums and semantic metadata checks. No custom pickle sandbox is attempted.

## Compatibility and inference bundle

Checksum-valid artifacts must also agree on model identity/checksum, target version,
2022-12-31 cutoff, 2023 window, threshold `.5`, cohort grain, feature/preprocessing and split
lineage, evaluation and explanation references, presentation schema/handoff, API version,
mandatory limitation, and the 8,416 feature/target cohort counts. The model's transformed
feature manifest is recomputed and must equal the registered explanation manifest.

`load_inference_bundle(registry, bundle_id)` returns a frozen `InferenceBundle`, not a loose
dictionary. It contains the validated manifest, read-only Phase 4B resources (model, indexed
feature rows, transformed-feature manifest, preferred-model handoff), and read-only
presentation handoff. Request processing does not mutate these structures.

`PredictionService.load` constructs a local store and registry, loads the explicit default
bundle once, and then depends only on the typed bundle. Routes retain the Phase 5A external
contract. `GET /api/v1/model` adds bundle ID and registry version; all prior fields remain.

## Validation and inspection

Validate or inspect the exact bundle outside API startup:

```console
python -m howreliable.modeling.registry validate howreliable-rf-2022-cutoff-v1
python -m howreliable.modeling.registry inspect howreliable-rf-2022-cutoff-v1
```

Both operations perform the same full load and print a compact validation summary. There are
no mutation, registration, promotion, rollback, automatic version-selection, or remote-fetch
commands.

## Limitations and Phase 6A handoff

The filesystem registry contains one research-checkpoint bundle, requires the complete local
artifact layout, and offers no concurrency protocol, remote availability, credentials,
encryption policy, lifecycle management, or production approval. Its manifest includes some
evaluation lineage artifacts needed to prove compatibility even though they are not retained
as large in-memory objects after validation.

Phase 6A receives the `ArtifactStore` operations, relative-key manifest format, explicit
bundle ID, twelve roles, sidecar manifest integrity rule, checksum-before-use policy, and
`load_inference_bundle` integration point. An object-store implementation must preserve those
semantics without changing `PredictionService` or the public API. Phase 6A has not started.
