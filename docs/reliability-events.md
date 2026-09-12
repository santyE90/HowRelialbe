# Reliability Events

`ReliabilityEvent` is the source-independent representation of one observed complaint,
failure, recall, manufacturer communication, or other reliability-related event. It is
immutable and remains distinct from the raw evidence used to create it. In Phase 1C only
NHTSA complaints are mapped; the other event types reserve stable vocabulary without
claiming those sources are implemented.

## Schema

Required fields are event type, canonical component, severity and its explicit evidence,
vehicle association, and source reference. Mileage, occurrence date, report date, and source
description are nullable because sources may not provide them. Serialization uses canonical
enum values, ISO 8601 date strings, explicit nulls, and computed stable identifiers.

Vehicle association preserves normalized make, model, year, and optional generation. It has
a stable broad identity. A known Phase 1A configuration ID is also retained when the source
explicitly supplies enough information to construct `Vehicle`; missing transmission,
drivetrain, engine, trim, or generation is never inferred.

## Taxonomies

Event types are `COMPLAINT`, `FAILURE`, `RECALL`, `MANUFACTURER_COMMUNICATION`, `OTHER`, and
`UNKNOWN`.

Canonical components are `ENGINE`, `TRANSMISSION`, `ELECTRICAL`, `COOLING`, `SUSPENSION`,
`BRAKES`, `STEERING`, `FUEL_SYSTEM`, `HVAC`, `EXHAUST`, `BODY`, `AIRBAGS_RESTRAINTS`,
`TIRES_WHEELS`, `OTHER`, and `UNKNOWN`. `UNKNOWN` means the source component is absent or
blank. `OTHER` means it is present but no conservative rule maps it. The original source
component is always retained.

NHTSA mapping uses an ordered table of exact component roots and documented hierarchical
separators (`:` and `,`). Examples include `SERVICE BRAKES` to `BRAKES`, `ELECTRICAL SYSTEM`
to `ELECTRICAL`, and `AIR BAGS` or `SEAT BELTS` to `AIRBAGS_RESTRAINTS`. It does not search
narratives, fuzzy-match text, or infer categories from words appearing in arbitrary places.

## Severity

Severity expresses explicit safety evidence, not repair cost, mechanical damage, failure
probability, or narrative sentiment. NHTSA indicators map in precedence order:

1. One or more deaths: `CRITICAL`.
2. Otherwise, one or more injuries: `HIGH`.
3. Otherwise, an explicit crash or fire: `MODERATE`.
4. Otherwise, explicit zero deaths/injuries and `N` crash/fire flags: `LOW`.
5. Otherwise: `UNKNOWN`.

The parsed death count, injury count, crash flag, and fire flag remain attached as severity
evidence so the derivation is inspectable.

## Mileage and dates

Blank mileage maps to `null`; an ASCII unsigned integer, including an explicit zero, maps to
an integer. Signs, decimals, surrounding whitespace, and nonnumeric values fail mapping.
There is no maximum-mileage or outlier policy in this phase. NHTSA's historical conversion
of some blank numeric fields to zero means source zero may not always imply measured zero.

Blank source dates map to `null`. Valid eight-digit `YYYYMMDD` values become date-only values
without timezone. Impossible dates and alternate formats fail mapping explicitly.

## Identity and traceability

The event identity key contains canonicalized source type, organization, dataset, and source
row identifier. Its event ID is a SHA-256 digest of that canonical JSON. For NHTSA, `CMPLID`
is the row-level identifier; `ODINO` remains a separate source reference because it may
repeat across component rows. Updateable mileage, dates, narrative, component, and severity
evidence are deliberately not identity inputs, so a corrected publication retains the same
event identity.

Mapped events retain source type, organization, dataset, `CMPLID`, `ODINO`, original NHTSA
component, and optional source artifact filename and SHA-256. They do not duplicate all 51
raw columns. Mapping failures carry stable reasons suitable for diagnostics and are never
silently dropped by the mapper.
