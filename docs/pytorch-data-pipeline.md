# Phase 3C PyTorch data pipeline

## Purpose and input contract

Phase 3C creates a reproducible PyTorch-ready data boundary for later neural-network
experiments. It performs no model construction, training, prediction, or evaluation. It
loads only the validated Phase 3B feature and split artifacts, the Phase 3A target, and the
Phase 3B result used as the comparison contract:

| Input | SHA-256 |
|---|---|
| `cohort-features-asof-2022-12-31.jsonl` | `1cffb203298af438639c102a924f205ffb8ec25e6ea87035e670cecc902dfdfe` |
| `future-complaint-activity-2022-12-31-12m.jsonl` | `56b04f3f842d4f857f39b050c310b6fee2a83b20b9afeefc82c5c5967c19864e` |
| `cohort-split-2022-12-31.jsonl` | `43211c19328054f21ed730da6b9e65b78fae6e4b0af5983411b2a6811b34297a` |
| `baseline-results.json` | `3fe3545d4890db16628102b6e3ad6489aa3e2443309535a894d56e33551481c2` |

Generation fails if a checksum, provenance version, cutoff, identity, static identity field,
split assignment, row count, or positive count differs. It never rebuilds source features
or creates a new split.

## PyTorch concepts in this project

A **Tensor** is the numeric multidimensional data supplied to PyTorch. Each cohort has one
90-element feature tensor and one separate one-element target tensor.

A **Dataset** maps an integer cohort index to its already-preprocessed feature tensor,
target tensor, stable cohort ID, and diagnostic metadata. Preprocessing is not repeated in
`__getitem__`.

A **DataLoader** groups Dataset items into batches. The **batch size** is the number of
cohorts processed together; Phase 3C uses 64. Training batches shuffle deterministically so
later optimization does not repeatedly see one fixed order. Validation and test loaders do
not shuffle, making diagnostic order stable.

The training split fits preprocessing parameters and will later fit model parameters. The
validation split is for later model selection. The test split stays untouched until final
Phase 3D evaluation. Features and targets use `torch.float32`, PyTorch's standard efficient
training representation. The target is separate so it can never become an input feature.
No oversampling, undersampling, weighting, or weighted sampler is used because the training
target is already approximately balanced.

## Feature selection and quality audit

The initial candidates are the 102 numeric/binary columns used by the strongest defensible
Phase 3B evidence experiment: static numeric context, complaint volume/recency/components/
severity/evidence/mileage, communications, and recalls. Production, target columns, raw
make/model strings, IDs, source-status strings, and date strings are not tensor inputs.
Make/model and source statuses remain item metadata; embeddings are deferred.

The training-only feature audit removes 12 constants and three exact duplicates. The
duplicates are `complaints_last_36m` (identical to historical complaint count in this source
window), historical death count (identical to critical-severity count), and communication
service-bulletin count (identical to unique communication count). Constant unknown-category
and unused communication-type columns are recorded individually in the manifest. No column
is all-missing. This leaves 87 source inputs; three missing indicators produce 90 tensor
features.

Transformed-family counts are:

| Family | Tensor features |
|---|---:|
| Static | 2 |
| Complaint volume | 4 |
| Complaint recency | 2 |
| Complaint components | 28 |
| Complaint severity/evidence/mileage | 15 |
| Communications | 18 |
| Recalls | 21 |

## Training-only preprocessing

- Fifty-nine nonnegative count-like features use `log1p` to reduce extreme right skew and
  are then standardized using training means and population standard deviations.
- Seven other continuous features are standardized from training statistics.
- Nineteen share features retain their natural 0–1 values without log transformation or
  standardization.
- Two source-observed binary features remain directly interpretable 0/1 values.
- Nullable historical mileage, communication recency, and recall recency use training
  medians plus explicit missing indicators.
- No clipping, winsorization, target encoding, frequency encoding, hashing, one-hot identity,
  or full-dataset preprocessing is performed.

There are 4,643 nullable selected values before preprocessing and zero afterward. Observed
zero remains distinct from missing: communication/recall observed flags and raw status
metadata preserve `OBSERVED_RECORDS`, `MATCHED_RECORDS_AFTER_CUTOFF_ONLY`, and
`NO_MATCHED_RECORD` semantics.

The deterministic manifest records every tensor index, source feature, family, type,
missingness policy, and transform. The preprocessor stores the feature order, medians,
post-log/imputation means and standard deviations, missing indicators, removals, cutoff,
input checksums, and version `pytorch-cohort-pipeline-1.0`.

## Dataset and DataLoader contract

`HowReliableCohortDataset` holds the transformed matrix in memory once. Each item contains:

- `features`: float32 tensor with shape `[90]`
- `target`: float32 tensor with shape `[1]`, suitable for later `BCEWithLogitsLoss`
- `cohort_id`: stable string identity
- `metadata`: make/model/year, age, historical support, communication/recall availability
  and source status, and frozen split

Loaders use batch size 64, `num_workers=0`, and `drop_last=False`. Train uses an explicit
seed-20220913 `torch.Generator` and `shuffle=True`; validation/test use `shuffle=False`.
The portable zero-worker policy avoids Windows/Linux worker-order differences.

| Split | Rows | Positives | Prevalence | Example feature shape | Target shape | Batch positives |
|---|---:|---:|---:|---|---|---:|
| Train | 5,891 | 3,078 | 52.2492% | `[64, 90]` | `[64, 1]` | 37 |
| Validation | 1,262 | 660 | 52.2979% | `[64, 90]` | `[64, 1]` | 33 |
| Test | 1,263 | 660 | 52.2565% | `[64, 90]` | `[64, 1]` | 36 |

All example feature and target tensors are `torch.float32`. Batch positive counts describe
only these deterministic example batches, not a balancing policy.

## Generated artifacts

Artifacts are small contracts rather than duplicate tensor copies and are Git ignored:

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `artifacts/modeling/pytorch/feature-manifest.json` | 37,398 | `61459c3c2b7056ed47d700772a9555658750a7be949fc94f340a877e61b9fbc1` |
| `artifacts/modeling/pytorch/preprocessor.json` | 41,663 | `55f57460896f589c02d55624c0ce5c516b7d7629fdb31d96de26ca6361d60f22` |
| `artifacts/modeling/pytorch/dataset-metadata.json` | 14,985 | `9f39b41a9d3f416610869f3410c0955749d9f53319c7354e5679973ad79144f3` |

Generate them once from the repository root with:

```console
python -m howreliable.modeling.pytorch
```

The command refuses to overwrite an existing artifact directory. Phase 3D can reload the
preprocessor and deterministically recreate the tensors from the canonical inputs without
persisting giant tensor copies.

## Determinism, limitations, and Phase 3D handoff

Tests prove that validation/test extreme values cannot change train medians, means,
standard deviations, missing indicators, or feature selection. Identical inputs and a fixed
clock produce byte-identical artifacts and tensors. Same-seed train loaders produce the same
first-epoch order; a different seed changes order but not membership. Validation/test order
is stable.

This pipeline inherits all Phase 3B limitations: one cutoff and random cohort split,
reporting/popularity bias, weak sparse and old-cohort behavior, no universal exposure, and a
target that is not repair, failure, health, safety, or reliability. Log transforms reduce
skew but do not remove outliers. Train-fitted quality removal may need reassessment for a
future cutoff, and exact model reproducibility still depends on compatible PyTorch/platform
versions.

Phase 3D selected models using validation only. Its comparison floor was validation
ROC-AUC .905985, PR-AUC .929263, and F1 .827751. Only after selection may final test metrics
be compared with ROC-AUC .892844, PR-AUC .917283, and F1 .820673. Aggregate improvement is
insufficient if age-21+ or low-support performance worsens. The completed Phase 3D MLP did
not clear that floor; the Phase 3C preprocessing and split remain unchanged. See
[first neural network](first-neural-network.md).

Phase 3E reloads and hashes these same manifest, preprocessing, dataset-metadata, target,
feature, and split contracts into each deterministic run identity. Resume and evaluation
reject changed lineage. See [training infrastructure](training-infrastructure.md).
