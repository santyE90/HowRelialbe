"""Command-line entry point for NHTSA complaint ingestion."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from howreliable.common import configure_logging
from howreliable.data.ingestion.nhtsa_complaints import (
    DEFAULT_REQUESTED_RANGE,
    DEFAULT_SOURCE_URL,
    IngestionError,
    download_artifact,
    ingest_local_artifact,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest official NHTSA ODI complaint data")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--artifact", type=Path, help="existing official ZIP artifact")
    source.add_argument("--download", action="store_true", help="download the official artifact")
    parser.add_argument(
        "--retrieved-at",
        type=datetime.fromisoformat,
        help="required acquisition timestamp for a local artifact (ISO 8601 with timezone)",
    )
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--requested-range", default=DEFAULT_REQUESTED_RANGE)
    parser.add_argument(
        "--limit",
        type=int,
        help="write a bounded diagnostic sample; provenance marks it incomplete",
    )
    parser.add_argument(
        "--raw-directory",
        type=Path,
        default=Path("data/raw/nhtsa/complaints"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/interim/nhtsa/complaints/complaints.jsonl"),
    )
    return parser


def main() -> int:
    """Run the narrow NHTSA ingestion workflow."""
    parser = _parser()
    arguments = parser.parse_args()
    logger = configure_logging()

    try:
        if arguments.download:
            filename = Path(arguments.source_url).name
            receipt = download_artifact(
                arguments.source_url,
                arguments.raw_directory / filename,
            )
            artifact = receipt.path
            retrieved_at = datetime.fromisoformat(receipt.retrieval_timestamp_utc)
        else:
            artifact = arguments.artifact
            retrieved_at = arguments.retrieved_at
            if retrieved_at is None:
                parser.error(
                    "--retrieved-at is required with --artifact; provenance is not guessed"
                )

        result = ingest_local_artifact(
            artifact,
            arguments.output,
            retrieval_timestamp=retrieved_at,
            source_url=arguments.source_url,
            requested_range=arguments.requested_range,
            record_limit=arguments.limit,
        )
    except (IngestionError, OSError, ValueError) as error:
        logger.error("ingestion failed: %s", error)
        return 1

    logger.info(
        "ingested %d records to %s (sha256=%s)",
        result.provenance.record_count,
        result.output_path,
        result.provenance.source_artifact_sha256,
    )
    logger.info("wrote provenance to %s", result.provenance_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
