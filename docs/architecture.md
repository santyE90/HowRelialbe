# Architecture

## Current implementation

Phase 0 contains only an installable Python package and reusable configuration and logging
foundations. Tests and static-analysis configuration enforce a reliable starting point. No
domain, data, model, API, deployment, or UI component is implemented.

## Planned system

The intended high-level flow is:

```text
raw automotive data
  -> cleaning
  -> feature engineering
  -> ML training
  -> evaluation
  -> versioned model artifact
  -> FastAPI inference
  -> Docker packaging
  -> AWS deployment
  -> minimal UI (later)
```

Each arrow represents a planned boundary, not current functionality. Data processing,
training, evaluation, and online inference will remain separable so they can be tested and
operated independently. Artifact metadata and data provenance will connect the stages rather
than hidden shared state. Concrete storage, service, and deployment designs will be selected
in their respective phases once requirements are known.

