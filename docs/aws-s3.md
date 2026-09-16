# Phase 6A AWS S3 artifact storage

## Purpose and storage architecture

Phase 6A implements cloud contract `howreliable-s3-1.0`. It adds an S3 implementation of
the Phase 5B artifact boundary; it does not add compute, deployment, networking, monitoring,
or infrastructure provisioning. The unchanged flow is:

```text
relative manifest keys -> configured ArtifactStore -> checksum-first ModelRegistry
  -> typed InferenceBundle -> application-lifetime PredictionService -> FastAPI
```

The canonical bundle remains `howreliable-rf-2022-cutoff-v1`. Its single manifest, with
SHA-256 `831ecaa2cf31102bb13d2e414a380465cb948b07c45f9a4acb225e40aa2159cc`,
works with both stores. Artifact locations remain relative logical keys; no manifest contains
an `s3://` URL.

## Configuration and object layout

Local is the safe default. S3 must be selected explicitly:

```text
HOWRELIABLE_ARTIFACT_BACKEND=local|s3
HOWRELIABLE_MODEL_BUNDLE_ID=howreliable-rf-2022-cutoff-v1
HOWRELIABLE_S3_BUCKET=<private-bucket>
HOWRELIABLE_S3_PREFIX=<optional/key/prefix>
HOWRELIABLE_AWS_REGION=<optional-region>
```

Given bucket `B` and prefix `P`, logical key `artifacts/x.json` resolves to object key
`P/artifacts/x.json` in `B`. The prefix is normalized by removing outer slashes. It is not a
filesystem path or public URL. The registry marker layout is:

```text
s3://B/P/artifacts/registry/bundles/howreliable-rf-2022-cutoff-v1/
  manifest.json
  manifest.sha256
```

The twelve registered artifact objects keep their manifest locations below the same prefix.
Absolute POSIX/Windows paths, leading slashes, `s3://` references, backslashes, parent
traversal, dot segments, and empty segments are rejected before an SDK call.

## Retrieval, integrity, and trusted deserialization

`S3ArtifactStore` uses `HeadObject` for existence, `GetObject` for bytes, and
`ListObjectsV2` with a delimiter for explicit bundle membership. SHA-256 is computed over
the actual downloaded bytes. ETag, Content-MD5, and object metadata are never authoritative.
The manifest checksum is established before manifest parsing, and all twelve artifact
checksums are established before JSON parsing or trusted joblib deserialization. Integrity
does not make arbitrary pickle safe; only the frozen project model is trusted.

At application construction, configuration selects `LocalArtifactStore` or
`S3ArtifactStore`, then the shared registry/loader builds one in-memory bundle. An explicitly
selected S3 backend never falls back to local files. Missing objects, access denial,
unavailable storage, malformed contracts, and checksum mismatches prevent startup. After a
successful load, requests perform no S3 calls.

## Credentials, IAM, privacy, and encryption

Boto3 uses its standard credential provider chain. Local options include an AWS CLI/shared
profile or standard environment credentials; a future deployed process should use an IAM
role. No access key, secret, token, or credential object belongs in source, `.env.example`,
logs, manifests, or artifacts.

The runtime reader policy template is
`infrastructure/iam/howreliable-s3-reader-policy.json`. Runtime needs only prefix-scoped
`s3:GetObject` and prefix-constrained `s3:ListBucket`, because explicit registry membership
uses `list`. It has no write, delete, bucket administration, or wildcard S3 permission. The
separate publishing identity additionally needs prefix-scoped `s3:PutObject`; that permission
must not be attached to the inference runtime.

The bucket must block public access. Inference uses IAM-authenticated SDK calls, never public
or presigned URLs. Publishing requests S3-managed server-side encryption (`AES256`/SSE-S3).
Bucket-level default encryption and the public-access block should also be enforced by the
AWS environment. KMS management is outside Phase 6A.

## Publishing and collision behavior

Publish only from a repository containing the validated local canonical bundle:

```console
python -m howreliable.cloud.s3 publish-bundle howreliable-rf-2022-cutoff-v1 \
  --bucket <bucket> --prefix <prefix> --region <region>
```

The command fully validates the local bundle first, then writes the twelve artifact objects,
`manifest.json`, and `manifest.sha256` last. Every newly uploaded object is downloaded and
hashed, then the complete remote bundle is loaded and cross-contract validated. Safe
metadata records SHA-256, bundle ID, and role, but correctness never depends on metadata.

Publication is idempotent. An existing object whose downloaded SHA-256 matches is skipped.
Different bytes at any frozen key cause a closed failure; Phase 6A has no overwrite option.
This is object-ordered fail safety, not a multi-object S3 transaction. A failed first publish
may leave matching objects, and a retry safely resumes them. The final marker is deliberately
last so an incomplete publication is less likely to look complete.

## Validation, inspection, and optional AWS smoke

Validate or inspect without mutation or training:

```console
python -m howreliable.cloud.s3 validate-bundle howreliable-rf-2022-cutoff-v1 \
  --bucket <bucket> --prefix <prefix> --region <region>
python -m howreliable.cloud.s3 inspect-bundle howreliable-rf-2022-cutoff-v1 \
  --bucket <bucket> --prefix <prefix> --region <region>
```

Both commands perform the complete checksum and compatibility load; inspection prints only
safe bundle metadata. Automated tests use an injected deterministic client and never contact
AWS. A real-AWS smoke is optional: use an existing private bucket/prefix and authorized
profile, publish, validate, start the API with the S3 variables, and request representative
cohorts. The tool never creates or deletes buckets or objects.

Tests establish that local and S3 loads have identical manifest/model/target/threshold/grain
contracts, cohort counts, representative probabilities, classifications, explanation
reconstruction, and limitation flags. The machine-readable contract is
`artifacts/cloud/s3-contract.json`.

## Limitations and Phase 6B handoff

Phase 6A supplies storage access, explicit backend/bucket/prefix/bundle configuration,
reader IAM requirements, startup loading, deterministic publication, and a validated
local/S3 equivalence contract. It does not provide S3 availability guarantees, replication,
retention/lifecycle policy, concurrent publisher locking, automatic rollback, key rotation,
promotion aliases, model approval, or deployment.

Phase 6B receives `S3ArtifactStore`, `howreliable-s3-1.0`, the private bucket/prefix layout,
explicit bundle ID, IAM reader boundary, startup fail-closed/load-once behavior, the S3
contract artifact/checksum, and equivalence evidence. Selection of EC2, ECS, Fargate, or any
other compute platform belongs exclusively to Phase 6B.
