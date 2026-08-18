"""Standalone CLI for the Worker Agent - runnable without Phase 3 changes.

    python -m worker_agent.cli "<task description>"
    python -m worker_agent.cli --task-file path/to/task.txt
    python -m worker_agent.cli "..." --out proposal.json

Prints the Proposal as JSON to stdout (so it can be piped straight into
whatever drives Phase 3's gate later) and, if --out is given, also writes
it to a file.

Requires GOOGLE_API_KEY (Gemini Developer API) or, for real Vertex AI,
GOOGLE_GENAI_USE_VERTEXAI=TRUE plus GOOGLE_CLOUD_PROJECT/GOOGLE_CLOUD_LOCATION
and application-default credentials. See README.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .orchestrator import run_worker_agent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="worker_agent",
        description="Draft a Sentry detection-rule patch for a task description, self-check it, and print the result as JSON.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("task", nargs="?", help="Task description text")
    group.add_argument("--task-file", help="Path to a file containing the task description")
    parser.add_argument("--out", help="Also write the resulting JSON to this file")
    args = parser.parse_args(argv)

    if args.task_file:
        task_description = Path(args.task_file).read_text().strip()
    else:
        task_description = args.task

    if not task_description:
        parser.error("task description is empty")

    proposal = run_worker_agent(task_description)
    output = proposal.model_dump_json(indent=2)

    print(output)
    if args.out:
        Path(args.out).write_text(output + "\n")

    return 0 if proposal.self_check_result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
