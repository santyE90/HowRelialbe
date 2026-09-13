"""Narrow CLI for Phase 2D EWR production exposure processing."""

from __future__ import annotations

import argparse
from pathlib import Path

from howreliable.data.exposure.nhtsa_ewr import ingest_snapshot, retrieve_snapshot, run_matching


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process NHTSA EWR light-vehicle production exposure"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    retrieve = subparsers.add_parser("retrieve")
    retrieve.add_argument("--raw-directory", type=Path, required=True)
    retrieve.add_argument("--report-years", type=int, nargs="+", default=[2003, 2014, 2025])
    ingest = subparsers.add_parser("ingest")
    ingest.add_argument("--manifest", type=Path, required=True)
    ingest.add_argument("--records", type=Path, required=True)
    ingest.add_argument("--cohorts", type=Path, required=True)
    ingest.add_argument("--provenance", type=Path, required=True)
    match = subparsers.add_parser("match")
    match.add_argument("--complaints", type=Path, required=True)
    match.add_argument("--production", type=Path, required=True)
    match.add_argument("--output", type=Path, required=True)
    match.add_argument("--diagnostics", type=Path, required=True)
    match.add_argument("--provenance", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "retrieve":
        print(retrieve_snapshot(args.raw_directory, report_years=args.report_years))
    elif args.command == "ingest":
        print(ingest_snapshot(args.manifest, args.records, args.cohorts, args.provenance))
    else:
        print(
            run_matching(
                args.complaints,
                args.production,
                args.output,
                args.diagnostics,
                args.provenance,
            )
        )


if __name__ == "__main__":
    main()
