"""CLI for Phase 2F NHTSA recalls."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from howreliable.data.recalls.nhtsa import (
    create_manifest,
    ingest_archives,
    match_cohorts,
    retrieve_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Process official NHTSA recall data")
    commands = parser.add_subparsers(dest="command", required=True)
    retrieve = commands.add_parser("retrieve")
    retrieve.add_argument("--raw-directory", type=Path, required=True)
    manifest = commands.add_parser("manifest")
    manifest.add_argument("--raw-directory", type=Path, required=True)
    manifest.add_argument("--retrieved-at", type=datetime.fromisoformat, required=True)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("--manifest", type=Path, required=True)
    ingest.add_argument("--campaigns", type=Path, required=True)
    ingest.add_argument("--applicability", type=Path, required=True)
    ingest.add_argument("--provenance", type=Path, required=True)
    match = commands.add_parser("match")
    match.add_argument("--complaints", type=Path, required=True)
    match.add_argument("--campaigns", type=Path, required=True)
    match.add_argument("--applicability", type=Path, required=True)
    match.add_argument("--production-matches", type=Path, required=True)
    match.add_argument("--communication-evidence", type=Path, required=True)
    match.add_argument("--applicability-matches", type=Path, required=True)
    match.add_argument("--cohort-recalls", type=Path, required=True)
    match.add_argument("--unmatched", type=Path, required=True)
    match.add_argument("--cross-source", type=Path, required=True)
    match.add_argument("--provenance", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "retrieve":
        print(retrieve_artifacts(args.raw_directory))
    elif args.command == "manifest":
        print(create_manifest(args.raw_directory, args.retrieved_at))
    elif args.command == "ingest":
        print(ingest_archives(args.manifest, args.campaigns, args.applicability, args.provenance))
    else:
        print(
            match_cohorts(
                args.complaints,
                args.campaigns,
                args.applicability,
                args.production_matches,
                args.communication_evidence,
                args.applicability_matches,
                args.cohort_recalls,
                args.unmatched,
                args.cross_source,
                args.provenance,
            )
        )


if __name__ == "__main__":
    main()
