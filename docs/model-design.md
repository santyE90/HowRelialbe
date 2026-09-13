# Model Design

The machine-learning target is intentionally not finalized. Defining it requires evidence
about available observations, labels, time coverage, and leakage risks.

Phase 2G found that the current sources do not observe significant repairs or verified
failures. The defensible immediate direction for Phase 3A is therefore a cohort-level future
complaint-activity target-design study using calendar year-end cutoffs. The outcome must be
described as reporting activity, not repair probability, failure probability, individual-
vehicle risk, or reliability.

The 2022-12-31 cutoff has 8,508 previously observed cohorts and complete 12- and 24-month
complaint-source windows. Phase 3A must still define eligibility, minimum prior support,
outcome form, and how to handle complaint absence as an unverified rather than healthy
negative. No target, threshold, split, or performance claim is established here. See the
[dataset review](dataset-review.md).

Traditional baseline models will be developed and evaluated before a PyTorch model. This
will establish an interpretable reference point and determine whether added model complexity
is justified. No model currently exists.
