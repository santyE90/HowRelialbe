# Phase 3F authoritative model evaluation

## Contract and method

Phase 3F evaluates, but does not retrain or retune, the frozen Phase 3B random forest and
Phase 3E MLP. `howreliable-evaluation-1.0` operates on two deterministic, exactly aligned
1,263-row TEST prediction artifacts containing 660 positives and 603 observed-zero negatives.
The threshold remains .5. Probability means the estimated probability that a make/model/
model-year cohort receives at least one accepted observed NHTSA complaint during 2023 given
evidence through 2022-12-31. It is not repair, failure, reliability, safety, or individual-
vehicle risk.

Uncertainty uses 1,000 deterministic cohort-level percentile-bootstrap resamples at seed
20220913. Paired comparisons resample the shared cohort indexes. Samples without both classes
are omitted where a metric is undefined. Critical subgroup intervals use the same method;
their small samples make them unstable.

## Aggregate results and uncertainty

| Model | Accuracy | Precision | Recall | F1 | ROC-AUC | PR-AUC | Brier |
|---|---:|---:|---:|---:|---:|---:|---:|
| Random forest | .8187 | .8493 | .7939 | .8207 | .8928 | .9173 | .1295 |
| MLP | .8219 | .8480 | .8030 | .8249 | .8915 | .9171 | .1301 |

| Metric | Forest 95% interval | MLP 95% interval |
|---|---:|---:|
| Accuracy | [.7973, .8401] | [.8005, .8417] |
| Precision | [.8214, .8771] | [.8201, .8754] |
| Recall | [.7630, .8238] | [.7718, .8325] |
| F1 | [.7971, .8433] | [.8019, .8463] |
| ROC-AUC | [.8740, .9096] | [.8718, .9081] |
| PR-AUC | [.9023, .9318] | [.9019, .9314] |
| Brier | [.1187, .1407] | [.1188, .1422] |

Paired MLP-minus-forest intervals are ROC-AUC `-.00137 [-.00789,.00453]`, PR-AUC
`-.00013 [-.00418,.00402]`, F1 `+.00423 [-.00735,.01453]`, and Brier
`+.00064 [-.00248,.00423]`. Every interval includes zero. The evidence demonstrates no
meaningful performance difference; it does not support promoting the more complex MLP.

## Calibration and threshold sensitivity

Forest Brier/ECE are `.12946/.03089`; MLP values are `.13010/.03366`. The forest is slightly
better calibrated, though differences are small. Ten equal-width bins conserve all rows.
Calibration is only against observed complaint reporting, never mechanical failure.

At thresholds `.3/.4/.5/.6/.7`, forest precision rises `.727/.791/.849/.895/.921` while
recall falls `.900/.856/.794/.748/.685`. MLP precision rises
`.746/.807/.848/.874/.903` while recall falls `.888/.841/.803/.768/.717`. This is descriptive
TEST sensitivity, not tuning; `.5` remains canonical. Lower thresholds flag more cohorts and
favor recall, while higher thresholds flag fewer and favor precision.

## Subgroups and uncertainty

The report contains complete accuracy, prevalence, predicted prevalence, precision, recall,
F1, ROC-AUC, PR-AUC, Brier, and confusion matrices for every established group. Selected
ROC-AUC/F1 pairs are:

| Group | Forest | MLP |
|---|---:|---:|
| Age 0-2 | .8999/.8480 | .8920/.8471 |
| Age 3-5 | .9136/.8843 | .9156/.8703 |
| Age 6-10 | .9181/.8571 | .9231/.8696 |
| Age 11-20 | .8673/.7775 | .8702/.7913 |
| Age 21+ | .7087/.3030 | .6496/.2941 |
| Support 1 | .6638/.0000 | .6441/.0000 |
| Support 2-4 | .7063/.3036 | .7055/.3833 |
| Support 5-9 | .7520/.7377 | .7323/.7407 |
| Support 10-49 | .7924/.9269 | .8137/.9253 |
| Support 50+ | undefined/1.0 | undefined/1.0 |
| Communications + recalls | .8931/.8551 | .8934/.8574 |
| Communications only | .8255/.6763 | .8413/.7133 |
| Recalls only | .7637/.2500 | .6962/.1667 |
| Complaints only/limited | .7481/.4000 | .7351/.4000 |

Support=1 recall/F1 intervals are `[0,0]` for both models. ROC-AUC intervals are
`[.5769,.7545]` forest and `[.5517,.7293]` MLP. Age-21+ forest ROC-AUC is
`[.5865,.8231]`; MLP is `[.5142,.7775]`. These wide ranges reinforce weakness and small-
sample instability rather than providing evidence of a neural improvement.

## Errors and agreement

Forest errors are 93 false positives and 136 false negatives; MLP errors are 95 and 130.
Forest false negatives concentrate in support 1/2-4 (`45/67`), and MLP false negatives do
likewise (`45/61`). False positives concentrate in support 5-9 and 10-49. High-confidence
forest errors include 26 false positives at probability >=.8 (25 support 10-49) and 41 false
negatives at <=.2 (29 support 1). MLP counts are 31 and 47 respectively. These are errors
against a noisy reporting target, not proof of mechanical correctness or error.

Class predictions agree on 95.88% of cohorts; probability correlation is .9808. Both are
correct on 1,010 rows, forest alone on 24, MLP alone on 28, and both wrong on 201. The 52
disagreements occur mostly at support 2-4 (22) and 5-9 (29), with none at support 1. This
does not indicate meaningfully different neural patterns.

## Decision and handoff

Random forest status is `PREFERRED`; MLP status is `COMPARISON`. Neither is production-ready.
The forest has slightly better ranking and calibration, comparable classification metrics,
better critical old/recalls-only behavior, lower complexity, and the paired bootstrap shows
no MLP advantage.

Supported claims are limited to observed future cohort complaint activity, frozen-2023 test
performance, and weak sparse-cohort performance. Unsupported claims include repair/failure
probability, individual risk, healthy/unhealthy status, reliability/safety probability,
causal recall/communication effects, and universal exposure-normalized risk.

Phase 4A must use the frozen preferred forest artifact, its serialized sklearn preprocessing,
the Phase 3B feature contract, target/split contract, threshold .5, and this evaluation report.
It may explain the model but must not rewrite evaluation or claim causal/reliability meaning.
No Phase 4A explainability is implemented here.

| Artifact | SHA-256 |
|---|---|
| `predictions/random-forest-test.jsonl` | `d015e95c3e79454666dff0610747bf8f51c8554a68e27d85a3a68fd9ec0fa7b4` |
| `predictions/first-mlp-test.jsonl` | `556dcac8169ffd827daeecf3f5f6c056193d5fa958ecb45ba14115ffe48ba621` |
| `evaluation-report.json` | `1d0c13477b2846d98fa54926e2bde6679cc9e68c539e11f26631fc332db673d8` |
| `preferred-model-handoff.json` | `2d66a8d7e7914d6c683b8359ca50f8f6eae83986f42c4e97fe796867ae7da871` |
