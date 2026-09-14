# Phase 3D first neural network

## Purpose and frozen input contract

Phase 3D asks whether a small PyTorch multilayer perceptron adds useful predictive value over
the frozen Phase 3B random forest. It predicts `future_12m_complaint_activity`: observed
cohort-level complaint reporting during 2023 given evidence available through 2022-12-31.
It does not estimate repair, failure, safety, individual-vehicle risk, health, or reliability.

The experiment reloads the Phase 3C `pytorch-cohort-pipeline-1.0` boundary: 90 float32 inputs,
5,891 training rows, 1,262 validation rows, 1,263 test rows, and the unchanged seed-20220913
split. Preprocessing remains training-only. The whole-history Phase 2G integrated artifact is
not a model input. The validated feature, target, and split SHA-256 values are respectively
`1cffb203298af438639c102a924f205ffb8ec25e6ea87035e670cecc902dfdfe`,
`56b04f3f842d4f857f39b050c310b6fee2a83b20b9afeefc82c5c5967c19864e`, and
`43211c19328054f21ed730da6b9e65b78fae6e4b0af5983411b2a6811b34297a`.

## Architecture and training policy

`HowReliableMLP` applies a linear layer, ReLU, and dropout for each hidden dimension, then
one final linear unit. Its forward pass returns one raw logit per row with shape `[batch, 1]`.
It deliberately has no sigmoid: `BCEWithLogitsLoss` combines sigmoid and binary cross entropy
more stably. Sigmoid is applied only when probabilities are needed for evaluation.

| Candidate | Hidden dimensions | Parameters | Dropout | Learning rate | Weight decay |
|---|---:|---:|---:|---:|---:|
| `mlp_64_32` | 64, 32 | 7,937 | .1 | .001 | .0001 |
| `mlp_64` | 64 | 5,889 | .1 | .001 | .0001 |
| `mlp_128_64` | 128, 64 | 19,969 | .2 | .0003 | .0001 |

All candidates use AdamW, batch size 64, a 100-epoch maximum, CPU execution, and a fixed
classification threshold of .5. Early stopping monitors validation ROC-AUC with patience 10
and minimum improvement .0001, retaining and restoring the best `state_dict`. Python, NumPy,
and PyTorch use seed 20220913 and deterministic PyTorch algorithms. Exact bitwise identity
outside the validated software/platform combination is not guaranteed by PyTorch.

Model selection never reads test outputs. It applies validation ROC-AUC, PR-AUC, F1, lower
Brier score, and parameter count in that order, treating differences within .0001 at each
stage as near-ties. The test split and its labels enter only the final selected-checkpoint
evaluation. The threshold remains .5 and was not tuned.

## Candidate results and overfitting

Metrics below are calculated after restoring each candidate's best validation checkpoint.

| Candidate | Best/stop epoch | Train loss | Validation loss | Val. accuracy | Precision | Recall | F1 | ROC-AUC | PR-AUC | Brier |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `mlp_64_32` | 3/13 | .4043 | .3775 | .8288 | .8616 | .8015 | .8305 | .904271 | .928454 | .121454 |
| `mlp_64` | 2/12 | .4110 | .3809 | .8265 | .8669 | .7894 | .8263 | .903413 | .926685 | .121958 |
| `mlp_128_64` | 7/17 | .4027 | .3757 | .8273 | .8733 | .7833 | .8259 | .904301 | .928372 | .120735 |

The 128/64 candidate's ROC-AUC advantage over 64/32 was only .000030, inside the declared
near-tie tolerance; PR-AUC was also a near-tie, and validation F1 selected 64/32. Thus the
selected architecture is `90 -> 64 -> 32 -> 1`, with 7,937 parameters. Its best checkpoint
was epoch 3 and early stopping activated at epoch 13. Training epoch loss continued from
.4975 at epoch 1 to .3698 at epoch 13 while validation loss worsened after the best region
and ROC-AUC declined to .8976. Widening delayed but did not improve generalization and added
12,032 parameters, so it supplied no evidence that extra capacity helped.

At the selected checkpoint, training ROC-AUC/PR-AUC/F1 were .891744/.915120/.813165 and
validation values were .904271/.928454/.830455. The higher validation values do not imply
inverse overfitting: the training population can be harder, and training and validation are
different samples. The epoch-level loss trajectory is the clearer overfitting evidence.

## Final metrics and random-forest comparison

The final selected model produced these test metrics once the architecture, preprocessing,
threshold, and primary-seed checkpoint were frozen:

| Split | Accuracy | Precision | Recall | F1 | ROC-AUC | PR-AUC | Brier | Predicted + | Confusion matrix |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Validation | .828843 | .861564 | .801515 | .830455 | .904271 | .928454 | .121454 | .486529 | `[[517,85],[131,529]]` |
| Test | .821853 | .848000 | .803030 | .824903 | .891474 | .917150 | .130097 | .494854 | `[[508,95],[130,530]]` |

Compared with the frozen random forest, neural-minus-forest deltas are:

| Split | ROC-AUC | PR-AUC | F1 | Brier (lower is better) |
|---|---:|---:|---:|---:|
| Validation | -.001714 | -.000809 | +.002704 | +.000524 |
| Test | -.001369 | -.000134 | +.004229 | +.000636 |

The MLP therefore did not beat the forest on validation ROC-AUC or PR-AUC and did not improve
Brier score. Its small F1 gains reflect the fixed .5 threshold, not a meaningful aggregate
win. Test ranking was also slightly worse. Added neural complexity is not justified for
prediction by these results.

## Seed robustness and calibration

After choosing the configuration, the same 64/32 model was trained at seeds 20220913 through
20220917. These runs were a robustness analysis, not a best-seed search; seed 20220913 remains
the canonical checkpoint.

| Validation metric | Mean | Population standard deviation |
|---|---:|---:|
| ROC-AUC | .904586 | .000731 |
| PR-AUC | .928919 | .000789 |
| F1 | .828083 | .002038 |
| Brier | .120869 | .000762 |

Best epochs were 3, 4, 7, 3, and 3. The low spread demonstrates stable training, but the
means remain slightly below the forest's ranking metrics. One seed exceeding a baseline
metric does not change the canonical result.

The test Brier score is .130097 versus .129460 for the forest. The ten-bin diagnostic accounts
for all 1,263 rows. The largest visible local gaps include `[.2,.3)` (mean prediction .2439,
observed .1709) and `[.8,.9)` (.8498 versus .7708); the top bin is close (.9773 versus .9746).
This is useful diagnostic evidence, not a calibration correction. Platt scaling and isotonic
regression were intentionally not introduced.

## Subgroup results

| Age | Rows/positive | ROC-AUC | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|
| 0-2 | 220/133 | .8920 | .8852 | .8120 | .8471 |
| 3-5 | 232/124 | .9156 | .9043 | .8387 | .8703 |
| 6-10 | 309/173 | .9231 | .8721 | .8671 | .8696 |
| 11-20 | 396/207 | .8702 | .7951 | .7874 | .7913 |
| 21+ | 106/23 | .6496 | .4545 | .2174 | .2941 |

Age 21+ worsened from forest ROC-AUC .7087 and F1 .3030. This is not an old-cohort
improvement.

| Historical complaints | Rows/positive | ROC-AUC | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|
| 1 | 276/45 | .6441 | .0000 | .0000 | .0000 |
| 2-4 | 330/84 | .7055 | .6389 | .2738 | .3833 |
| 5-9 | 197/114 | .7323 | .6977 | .7895 | .7407 |
| 10-49 | 303/260 | .8137 | .8609 | 1.0000 | .9253 |
| 50+ | 157/157 | undefined | 1.0000 | 1.0000 | 1.0000 |

For support 1, ROC-AUC fell from .6638 and F1 remained zero: the MLP found none of 45
positives. For support 2-4, F1 improved from .3036 to .3833 while ROC-AUC was essentially
flat (.7063 to .7055). That isolated threshold-level gain does not establish broad sparse-
cohort improvement. ROC-AUC is undefined for support 50+ because every row is positive.

| Source coverage | Rows/positive | ROC-AUC | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|
| Communications + recalls | 903/558 | .8934 | .8636 | .8513 | .8574 |
| Communications only | 188/76 | .8413 | .7612 | .6711 | .7133 |
| Recalls only | 97/18 | .6962 | .3333 | .1111 | .1667 |
| Complaints only/limited | 75/8 | .7351 | 1.0000 | .2500 | .4000 |

Communications-only performance improved over the forest (ROC-AUC +.0157, F1 +.0370), while
recalls-only worsened (ROC-AUC -.0675, F1 -.0833). These smaller groups should not be
overinterpreted.

## Signal diagnostic, artifacts, and conclusion

One deterministic validation permutation per feature family reduced ROC-AUC most for
complaint components (.0550) and complaint volume (.0453), followed by complaint severity
(.0161), recency (.0128), communications (.0127), static fields (.0067), and recalls (.0057).
Correlated features make these descriptive sensitivities, not causal or independent effects.
They confirm that complaint persistence dominates while communications and recalls remain
modestly useful.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `artifacts/models/pytorch/first-mlp.pt` | 36,077 | `0d6c41c88a2fd9f82a8be167a8097912ad13682fba040a76888d7b750a9cd014` |
| `artifacts/models/pytorch/first-mlp-history.json` | 6,179 | `e1b239636de450d165598484288d4fd98f569fa1f244fcc148205f2a6bee2287` |
| `artifacts/models/pytorch/first-mlp-results.json` | 30,176 | `ebcd258642d0e2d9c9366c5005187ab4ba58c09b2462c8c2d0ed280ba34652ee` |

The checkpoint is a safely reloadable `state_dict` plus explicit architecture, data-contract,
training, best-epoch, seed, and PyTorch-version metadata; it is not a pickled module. Run the
bounded experiment once with `python -m howreliable.modeling.pytorch.train` after generating
Phase 3C artifacts. Existing output causes an error rather than replacement.

The exact Phase 3E recommendation is engineering-only: preserve the random forest as the
predictive benchmark (validation/test ROC-AUC .905985/.892844, PR-AUC .929263/.917283, and
test Brier .129460) and preserve the Phase 3D MLP as a reproducibility fixture. Any training
infrastructure must retain validation-only selection, single final test evaluation, seed
reporting, and age/support/source diagnostics. It should not imply that a more elaborate
neural trainer will improve prediction; new neural work needs material, stable aggregate and
sparse/old-cohort gains before displacing the forest.
