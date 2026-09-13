# Phase 2D NHTSA EWR Production Exposure

Phase 2D evaluates whether official production counts can provide a defensible exposure
proxy for Phase 2C complaint cohorts. It does not create a reliability target or alter the
Phase 2C feature tables.

## Official source and observed distribution

The source is NHTSA's public Early Warning Reporting (EWR) interface:

- [NHTSA EWR overview](https://www.nhtsa.gov/vehicle-manufacturers/early-warning-reporting)
- [NHTSA EWR FAQ](https://www.nhtsa.gov/early-warning-reporting/ewr-frequently-asked-questions)
- [Official public EWR API](https://api.nhtsa.gov/ewr/manufacturers)
- [NHTSA EWR vehicle compendium](https://static.nhtsa.gov/odi/ewr/Documents/EWRVehicleCompendium.pdf)

The current distribution is a paginated JSON API behind NHTSA Data Search, not a single
CSV or archive. `/ewr/manufacturers` enumerates reporting entities and report types;
`/ewr/manufacturers/{id}` supplies available year/quarter report descriptors and current
report IDs; and `/ewr/productions` returns one selected report. Production requests require
`manufacturerId`, `reportId`, and category `L` for light vehicles.

The inspected production-row fields are `make`, `model`, `modelYear`, `reportCategory`,
`totalProduction`, and `typeCode`, with `platform` and `fuelPropulsionSystem` where supplied.
The two optional fields reveal four observed historical schema variants: both present,
either one present, or neither present. NHTSA does not publish an API schema version in the
responses, so provenance records `null`; it does not invent a version.

The manufacturer endpoint exposes one current report descriptor per entity/category/period.
Revised filings can replace earlier versions under NHTSA's submission process; the public
API did not expose parallel versions for the same selected filing. Report ID and the exact
raw response are retained so a later snapshot can be compared. Missing optional JSON fields
remain `null`; numeric values are accepted only as nonnegative JSON integers.

## Reporting and production semantics

NHTSA describes light-vehicle production reporting as quarterly. Its current overview lists
production reporting for light-vehicle manufacturers producing 5,000 or more vehicles
annually; lower-volume manufacturers have narrower EWR obligations. Reporting thresholds
have changed historically, and observed public records do not establish that every make is
in or out of scope. An absent row is therefore never treated as zero production or
definitive proof that a manufacturer was outside the rule.

The official vehicle compendium states that production is cumulative through the reporting
period for a current model year, or total model-year production after production ceases.
Consequently, quarters and historical snapshots must not be summed. HowReliable? selects the
latest selected period for each source configuration:

`reporting entity + make + model + model year + platform + type + fuel/propulsion`

It then sums distinct configurations at the Phase 2C `make + model + model year` grain.
This preserves the latest cumulative/final value while allowing a broad complaint cohort to
cover source-supported configurations. Every contributing source-record ID and reporting
period remains on the aggregate.

## Bounded snapshot and provenance

The validated snapshot selects Q4 reports in 2003, 2014, and 2025 where an entity has a
public category-L filing. These spaced snapshots span rolling model-year windows across the
public EWR era without acquiring every redundant quarter. Observed model years are
1994-2027. This intentionally cannot cover older complaint cohorts.

Each exact API response is immutable beneath `data/raw/nhtsa/ewr/production/`. The manifest
records its official URL and SHA-256 plus the snapshot UTC timestamp, selected periods,
category, adapter version, and completeness for the bounded selection. Interim canonical
records and processed aggregates have checksummed provenance. Commands refuse overwrite.

```console
python -m howreliable.data.exposure retrieve \
  --raw-directory data/raw/nhtsa/ewr/production/2026-09-12-q4-spaced-complete \
  --report-years 2003 2014 2025
python -m howreliable.data.exposure ingest \
  --manifest data/raw/nhtsa/ewr/production/2026-09-12-q4-spaced-complete/snapshot-manifest.json \
  --records data/interim/nhtsa/ewr/production/production-records.jsonl \
  --cohorts data/processed/exposure/nhtsa-ewr-production-cohorts.jsonl \
  --provenance data/processed/exposure/nhtsa-ewr-production.provenance.json
python -m howreliable.data.exposure match \
  --complaints data/processed/features/nhtsa-vehicle-cohorts.jsonl \
  --production data/processed/exposure/nhtsa-ewr-production-cohorts.jsonl \
  --output data/processed/exposure/complaint-production-matches.jsonl \
  --diagnostics data/processed/exposure/unmatched-ambiguous.jsonl \
  --provenance data/processed/exposure/complaint-production-matches.provenance.json
```

## Canonical representation and matching

`CanonicalProductionRecord` is immutable and separate from `ReliabilityEvent`. It preserves
source/report identity, reporting entity and period, original and normalized make/model,
model year, configuration fields, and production count. Normalization is only Unicode NFKC,
whitespace trim/collapse, and case folding. Original labels remain intact.

Matching is deterministic at normalized make/model/model-year grain. Statuses are `EXACT`,
`EXPLICIT_ALIAS`, `AMBIGUOUS`, and `NO_PRODUCTION_RECORD`. Exact matching runs first. An
explicit alias layer exists but the validated run uses an empty mapping: no source evidence
was judged strong enough to make brand/model aliases automatically. There is no fuzzy,
substring, edit-distance, embedding, or inferred manufacturer matching. Multiple candidates
are ambiguous rather than silently chosen.

Unmatched diagnostics distinguish absent makes, absent models within a present make, model
years outside the selected source, and absent make/model/year combinations. These are
diagnostic classes, not claims about legal reporting scope.

For a single exact or explicit-alias match with production greater than zero, the diagnostic
may contain `complaints_per_10k_produced`. This is complaint reporting volume divided by a
production proxy, not repair incidence, failure probability, risk, or reliability. Reported
zero remains zero and produces a null rate. Missing and ambiguous production remain null.

## Validated result

The snapshot contains 144 selected reports from 81 reporting entities and 27,606 parsed
source rows. It yielded 24,443 production cohorts from 188 makes and 6,312 make/model pairs.
There were 94 older repeated configuration rows across spaced snapshots; latest-period
selection removed them without summing. The retained 27,512 configurations include 2,511
cohorts composed of multiple source configurations. All source rows parsed successfully.

Against all 10,670 Phase 2C cohorts, 4,775 (44.7516%) matched exactly and 5,895 did not.
No aliases or ambiguous matches occurred. Matched cohorts contain 77.5753% of the 412,866
complaint events. All 4,775 matched denominators were positive and support the diagnostic
per-10k value; its minimum/median/maximum are 0.0236, 5.9119, and 9,613,333.33. The extreme
maximum illustrates why the value must not be interpreted as reliability.

Unmatched classes were 2,624 absent makes, 2,211 absent models for a present make, 980 absent
make/model/year combinations, and 80 model years outside the selected source. Common
unmatched labels include BMW, Mercedes-Benz, Forest River, Ford, Kia, Harley-Davidson,
Lexus, Chevrolet, Honda, Audi, Volkswagen, Toyota, and GMC. Examples such as source `CHEV`
versus complaint `chevrolet`, hyphenated versus unhyphenated Mercedes-Benz labels, model
suffixes, powertrain labels, RV/platform names, and motorcycle/heavy-vehicle cohorts explain
some—not all—gaps. They are quantified rather than guessed into matches.

## Limitations and recommendation

- Production is not active fleet size, registrations, vehicle-years, mileage, or usage.
- Complaints are self-selected allegations, not verified failures or repairs.
- Model years have unequal time at risk, especially recent years.
- EWR scope and historical reporting changes can omit entities or categories.
- Public filing revisions require snapshot comparison; the API exposes current descriptors.
- Source and complaint naming differ, and no fuzzy reconciliation is performed.
- A missing match means unknown exposure here, never zero vehicles produced.

EWR production is useful but incomplete: event-weighted coverage is materially better than
cohort coverage, yet 22.42% of complaint events and 55.25% of cohorts lack a defensible
denominator. It is strong enough to evaluate as an optional, coverage-gated future feature,
but not as a universal denominator and not without match status and missingness. Phase 3A
has not started.

Phase 2E subsequently combines only Phase 2D match availability with manufacturer-
communication availability in a separate coverage diagnostic. It does not change production
records, production matches, or the interpretation of the exposure proxy. See
[Manufacturer Communications](manufacturer-communications.md).
