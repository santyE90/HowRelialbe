# Phase 4A model explainability

Phase 4A explains the frozen preferred Phase 3B random forest without retraining it. SHAP was
not installed, and adding it was unnecessary dependency risk. The selected deterministic
method decomposes every tree path into changes in positive-class probability and averages
those changes across 200 trees. Baseline plus contributions reconstructs each probability;
the representative maximum error is `1.11e-16`. A ten-repeat, seed-20220913 permutation
importance on frozen VALIDATION cross-checks global rankings without fitting.

The 105-input manifest maps every transformed value and missingness indicator to its raw
feature, family, evidence source, readable label, value type, semantics, and caveat. Global
mean absolute probability contributions rank complaint volume (.1325), complaint components
(.1056), recency (.0948), severity/evidence/mileage (.0931), communications (.0601), recalls
(.0210), and static context (.0136). By source: complaints .4260, communications .0601,
recalls .0210, static .0136. These magnitudes are descriptive and not normalized shares.

The strongest individual inputs are 36-month complaint activity (.03955), unique historical
complaint identity count (.03924), historical complaint count (.03908), 24-month activity
(.03729), years observed (.03499), `OTHER` component count (.03269), low-severity count
(.03202), complaint recency (.01915), 12-month activity (.01799), and mileage evidence count
(.01717). Permutation broadly agrees at the complaint-persistence/family level; its leaders
include unique complaint identities, recency, years observed, and 24/12-month activity.
Correlated volume, recency, component, communication, and recall inputs redistribute both
measures, so individual ranks are not causal or independently identifiable.

Six deterministic TEST examples cover highest-probability observed positive, lowest-
probability observed zero, highest-probability false positive, lowest-probability false
negative, lowest-probability support=1, and lowest-probability age-21+. Each records top
positive/negative features, feature values, family/source sums, baseline, reconstruction,
and caveats. The high-confidence false positive is driven mainly upward by complaint volume,
recency, and components; the false negative is driven downward by sparse complaint history
and related evidence. These are errors relative to observed reporting, not mechanical truth.

Support=1 and age-21+ explanations explicitly warn that historical evaluation is weak for
their evidence profile. Drivers do not become a confidence score. Safe language is “this
feature contributed to the predicted future complaint-activity probability.” Prohibited
language includes risk/failure factors, repair probability, reliability explanations, safety
probability, or causal claims.

Artifacts under `artifacts/explainability/` are Git ignored: feature manifest
`ce0bf893…ef9`, global importance `a4c89ad…13bf`, representative explanations
`11c8e56d…d183`, and report `ce1bc690…5b95`.

Phase 4B should be renamed or reframed from “Risk Scoring” to something like “Complaint-
Activity Presentation and Decision Contract.” It must consume the frozen forest, threshold,
evaluation report, explanation manifest/method, limitation flags, and supported/prohibited
language. It must not invent a reliability/risk/confidence score. Phase 4B is not implemented.
