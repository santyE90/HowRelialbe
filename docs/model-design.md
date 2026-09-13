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
eligibility, alternatives, and Phase 3B leakage contract are defined in
[target definition](target-definition.md). No split, feature matrix, or model exists yet.

Traditional baseline models will be developed and evaluated before a PyTorch model. This
will establish an interpretable reference point and determine whether added model complexity
is justified. No model currently exists.
