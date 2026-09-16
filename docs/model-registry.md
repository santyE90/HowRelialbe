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

Phase 6A implements this seam with `S3ArtifactStore`, mapping each unchanged logical key
under an explicit bucket/prefix. S3 resolution returns a backend-specific bucket/key identity,
not a fake local path or public URL. See [AWS S3 artifact storage](aws-s3.md).

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

`PredictionService.load` explicitly selects a local or S3 store from settings, loads the
configured explicit bundle once, and then depends only on the typed bundle. S3 failure never
falls back to local files. Routes retain the Phase 5A external contract.

## Validation and inspection

Validate or inspect the exact bundle outside API startup:

```console
python -m howreliable.modeling.registry validate howreliable-rf-2022-cutoff-v1
python -m howreliable.modeling.registry inspect howreliable-rf-2022-cutoff-v1
```

Both local operations perform the same full load and print a compact validation summary.
Phase 6A adds separate S3 publish/validate/inspect commands; there is still no registration,
promotion, rollback, or automatic version selection.

## Limitations and Phase 6A implementation

The registry still contains one research-checkpoint bundle and no promotion lifecycle or
production approval. S3 improves location portability but does not create availability,
retention, locking, or deployment guarantees. The same relative-key manifest, explicit ID,
twelve roles, sidecar integrity rule, checksum-before-use policy, and typed loader are used
by both storage backends.
