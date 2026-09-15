# Phase 3E training infrastructure

## Purpose

Phase 3E turns the validated Phase 3D MLP into a reproducible, resumable, and auditable
training run. It is engineering work, not a new model search. The random forest remains the
predictive benchmark, while the MLP remains a comparison and reproducibility fixture. No
target, split, feature, architecture, threshold, or scientific conclusion changes.

The infrastructure version is `pytorch-training-1.0`. It deliberately avoids Hydra, MLflow,
W&B, trainer frameworks, sweep engines, cloud jobs, databases, schedulers, distributed
training, and model registries.

## Configuration and run identity

`TrainingConfig` is an immutable validated dataclass with architecture name, hidden
dimensions, dropout, learning rate, weight decay, batch size, maximum epochs, early-stopping
patience/minimum improvement, seed, device, threshold, and selection metric. JSON files must
contain exactly that schema. The default reproduces Phase 3D: `[64, 32]`, dropout `.1`, AdamW
at `.001` with weight decay `.0001`, batch 64, 100 epochs maximum, patience 10, minimum
ROC-AUC improvement `.0001`, seed 20220913, CPU, threshold `.5`, and validation ROC-AUC
selection.

The run ID is the SHA-256 of canonical sorted JSON containing the complete configuration,
training-infrastructure version, target version/checksum, source feature checksum, feature
manifest checksum, preprocessor checksum, dataset metadata checksum, and split checksum.
Wall-clock timestamps are excluded. Changing a meaningful configuration or lineage value
changes the ID; repeating the same inputs does not.

## Run directory and lifecycle

Runs live under `artifacts/training/runs/<run_id>/`:

```text
config.json
run-metadata.json
history.json
latest-state.pt
best-checkpoint.pt
validation-results.json
test-evaluation.json       # only after an explicit test evaluation
```

Status transitions are `CREATED -> RUNNING -> COMPLETED`, with `INTERRUPTED` for a resumable
bounded stop and `FAILED` for an exception. Failure metadata records error type, concise
message, and last completed epoch before re-raising. Existing run directories and evaluation
artifacts are not overwritten. Completed runs cannot resume.

Important JSON and torch state transitions write a complete temporary file in the same
directory, flush JSON to disk, and atomically replace the destination. The checkpoint stores
a `state_dict`, never a complete `nn.Module`.

## Best checkpoint, history, and resumable state

The best checkpoint contains model state, epoch, architecture, input dimension, AdamW
metadata, full training configuration, target version/cutoff, complete input lineage,
infrastructure version, validation metrics, and torch version. Highest validation ROC-AUC
with minimum improvement `.0001` remains the only checkpoint criterion; TEST is absent.

Each history epoch records train loss, validation loss/accuracy/precision/recall/F1/ROC-AUC/
PR-AUC/Brier, and learning rate. Batch logs are not persisted.

`latest-state.pt` is distinct from the best model. It contains current model and optimizer
states, current epoch, best epoch/metric/state, early-stopping counter, history, configuration
checksum, lineage, and Python/NumPy/torch/train-loader-generator RNG states. Resume reloads
the frozen configuration and current pipeline, rejects any incompatible contract, restores
all states, and starts at the exact next epoch. A synthetic five-epoch test proves that a
two-epoch interruption plus resume produces byte-identical JSON history and identical final
best-state tensors to continuous training.

Deterministic equality is validated on CPU with the pinned local versions. PyTorch does not
promise bitwise equality across materially different platforms, builds, or devices.

## Training and evaluation separation

Training validates Phase 3C, derives the ID, creates metadata, trains only with TRAIN and
VALIDATION, persists validation results, and completes without accessing TEST predictions.
The default evaluation operation is VALIDATION. TEST requires the deliberate
`--split test` argument, operates only on a completed best checkpoint, and writes a separate
overwrite-protected result containing metrics, calibration, established subgroups, signed
random-forest deltas, and the promotion-policy decision. TRAIN cannot be presented as final
evaluation.

```console
python -m howreliable.modeling.pytorch.training_cli train
python -m howreliable.modeling.pytorch.training_cli resume artifacts/training/runs/<run_id>
python -m howreliable.modeling.pytorch.training_cli evaluate artifacts/training/runs/<run_id>
python -m howreliable.modeling.pytorch.training_cli evaluate artifacts/training/runs/<run_id> --split test
python -m howreliable.modeling.pytorch.training_cli inspect artifacts/training/runs/<run_id>
```

`inspect` reports identity, status, configuration, lineage, epochs, metrics, and artifact
checksums without loading data or training.

## Baseline comparison and promotion policy

The reusable comparison reports neural-minus-random-forest ROC-AUC, PR-AUC, F1, and Brier.
It does not declare a winner from a tiny F1 difference. The forest remains preferred unless
future evidence demonstrates seed-stable, meaningful aggregate improvement, no material
calibration degradation, and no material deterioration for support=1 or age 21+. These are
conservative decision criteria, not invented scientific thresholds.

## Canonical reproduction

Canonical run ID:
`a6db5f7eadae67fe1c0a1f63f01682c889b4718483027fe50cc3fc25921222fd`.

The new training command reproduced best epoch 3, stopping epoch 13, validation ROC-AUC
`.9042711165`, PR-AUC `.9284535577`, F1 `.8304552590`, and Brier `.1214535626` exactly.
Only after completion, one explicit TEST operation reproduced ROC-AUC `.8914744460`, PR-AUC
`.9171495215`, F1 `.8249027237`, and Brier `.1300965484` exactly. All deltas from Phase 3D
are zero. A second independent validation-only real run produced the same run ID, byte-
identical history, identical validation metrics, and identical best-state tensors.

The canonical configuration content hash is
`e1fd47b2b4be5f7d270391ab26a31444010dde7c175559193218687fbeb75ece`.
Its generated artifacts are:

| Artifact | SHA-256 |
|---|---|
| `config.json` | `feaadf3c1dac30e92f69384650528e434eaf999ee3dc19a6d37c0dba9e7ce337` |
| `run-metadata.json` | `8a032dece053fe8ac75ebec04c6a6429b8e24d6ea30654ba51d5b94b98331407` |
| `history.json` | `345cee126b7236cca08db12b14940926dbee80273f967a68b1c3852a1bfe7fba` |
| `latest-state.pt` | `6f8dbe4564e7033d03682b6d7d4491ecceeabac716cfd7dee88f0f6fd247089a` |
| `best-checkpoint.pt` | `1f88901dc0bf05d27efb5a82a7d937dfb1b2a0744181b345f3f4444bdf8e5beb` |
| `validation-results.json` | `33290e47da125735cf154fce7468b42136dafb078774bc59928b4d31764593a5` |
| `test-evaluation.json` | `c1dcad6c0e25cb9119636d3c3b1db9c9d160e00cd18d0519914bc3c679805632` |

The reproducibility manifest records config/run identity, full lineage, Python, NumPy,
PyTorch, and sklearn versions, seed, device, Git commit when available, and start/completion
UTC. Generated run artifacts remain Git ignored.

## Limitations and Phase 3F handoff

Resume is local-file and epoch-boundary only; it does not recover the middle of a batch.
Atomic replacement protects completed file transitions but cannot make an entire multi-file
run transaction atomic. CUDA is accepted only when available and is not promised to match
CPU bitwise. There is no concurrent writer coordination, remote storage, registry, or job
orchestration. The model retains every Phase 3D scientific limitation.

Phase 3F should consume completed, lineage-validated evaluation artifacts to deepen model
evaluation without changing the target or retroactively selecting on TEST. It must preserve
the forest benchmark, explicit subgroup/calibration reporting, and the MLP's status as a
reproducibility fixture. Phase 3E does not implement Phase 3F.

Phase 3F now consumes the completed frozen run without retraining it. See
[model evaluation](model-evaluation.md) for the authoritative paired result.
