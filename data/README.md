# Local data layout

Downloaded and generated data are intentionally excluded from Git.

```text
data/
  raw/nhtsa/complaints/       # immutable official ZIP artifacts
  interim/nhtsa/complaints/   # deterministic JSON Lines and provenance
  processed/                  # reserved for later phases
```

Never edit or overwrite a raw artifact. Retrieve a newly published artifact under its
official filename and use its SHA-256 checksum and provenance sidecar to distinguish it.

