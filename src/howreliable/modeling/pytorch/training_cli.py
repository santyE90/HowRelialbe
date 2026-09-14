"""Narrow command line interface for Phase 3E training runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from howreliable.modeling.pytorch.training_config import TrainingConfig
from howreliable.modeling.pytorch.training_infrastructure import (
    create_training_run,
    evaluate_training_run,
    inspect_training_run,
    resume_training_run,
)


def _config(path: Path | None) -> TrainingConfig:
    if path is None:
        return TrainingConfig()
    return TrainingConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--pipeline-artifacts", type=Path, default=Path("artifacts/modeling/pytorch")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="HowReliable Phase 3E training infrastructure")
    commands = parser.add_subparsers(dest="command", required=True)
    train = commands.add_parser("train", help="create and train a deterministic run")
    _common(train)
    train.add_argument("--runs-root", type=Path, default=Path("artifacts/training/runs"))
    train.add_argument("--config", type=Path)
    resume = commands.add_parser("resume", help="resume an incomplete compatible run")
    _common(resume)
    resume.add_argument("run_directory", type=Path)
    evaluate = commands.add_parser("evaluate", help="evaluate a completed best checkpoint")
    _common(evaluate)
    evaluate.add_argument("run_directory", type=Path)
    evaluate.add_argument("--split", choices=("validation", "test"), default="validation")
    inspect = commands.add_parser("inspect", help="inspect run state without loading data")
    inspect.add_argument("run_directory", type=Path)
    arguments = parser.parse_args()

    if arguments.command == "train":
        result = create_training_run(
            arguments.repository_root,
            arguments.pipeline_artifacts,
            arguments.runs_root,
            _config(arguments.config),
        )
    elif arguments.command == "resume":
        result = resume_training_run(
            arguments.repository_root,
            arguments.pipeline_artifacts,
            arguments.run_directory,
        )
    elif arguments.command == "evaluate":
        result = evaluate_training_run(
            arguments.repository_root,
            arguments.pipeline_artifacts,
            arguments.run_directory,
            split=arguments.split.upper(),
        )
    else:
        result = inspect_training_run(arguments.run_directory)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
