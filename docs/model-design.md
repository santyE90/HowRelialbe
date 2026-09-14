# Model Design

Phase 3A selects `future_12m_complaint_activity` as the first supervised-learning target.
For an eligible make/model/model-year cohort at 2022-12-31, it records whether at least one
accepted NHTSA complaint is reported during 2023.

Phase 2G found that the current sources do not observe significant repairs or verified
failures. The defensible immediate direction for Phase 3A is therefore a cohort-level future
complaint-activity target-design study using calendar year-end cutoffs. The outcome must be
described as reporting activity, not repair probability, failure probability, individual-
vehicle risk, or reliability.

The rule yields 8,416 eligible cohorts, 4,398 positives, and 4,018 observed-zero rows. Zero
means no future report was observed; it is not a healthy or no-failure label. The target,
eligibility, alternatives, and the leakage contract are defined in
[target definition](target-definition.md).

Phase 3B implements the traditional baselines before any PyTorch work. It uses a separate
as-of-2022-12-31 feature matrix and frozen seed-20220913 split. The validation-selected
random forest reaches validation ROC-AUC .9060 and test ROC-AUC .8928. Logistic ablations
show only modest incremental signal from communications, recalls, and make/model identity;
performance is weak for old and sparse cohorts. See [baseline models](baseline-models.md).

Phase 3C may proceed only as a controlled comparison using the identical split. Neural
complexity is not presumed useful: it must beat the frozen forest and improve subgroup
behavior without changing the target or exploiting whole-history inputs.

Phase 3C now supplies that data boundary: 87 selected source inputs plus three missing
indicators form a 90-dimensional float32 tensor. Count-like fields use `log1p`; learned
medians and standardization statistics use training rows only. Raw make/model identity stays
outside the tensor, and no model or loss is implemented. See
[PyTorch data pipeline](pytorch-data-pipeline.md).
