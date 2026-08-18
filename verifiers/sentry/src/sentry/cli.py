"""Command-line interface for Sentry."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sentry.engine import Match, RuleLoadError, load_rules, scan

SEVERITY_ORDER = ["HIGH", "MEDIUM", "LOW"]


def _read_input(source: str | None, use_stdin: bool) -> str:
    if use_stdin:
        return sys.stdin.read()

    if source is None:
        raise SystemExit("error: provide TEXT_OR_FILE or use --stdin")

    path = Path(source)
    if path.is_file():
        return path.read_text()
    return source


def _print_findings(matches: list[Match]) -> None:
    if not matches:
        print("No findings.")
        return

    by_severity: dict[str, list[Match]] = {sev: [] for sev in SEVERITY_ORDER}
    for m in matches:
        by_severity.setdefault(m.severity, []).append(m)

    for severity in SEVERITY_ORDER:
        findings = by_severity.get(severity, [])
        if not findings:
            continue
        print(f"[{severity}] ({len(findings)} finding{'s' if len(findings) != 1 else ''})")
        for m in findings:
            print(f"  - {m.rule}: {m.description}")
            print(f"      span={m.start}:{m.end} matched={m.text!r}")
        print()


def cmd_scan(args: argparse.Namespace) -> int:
    text = _read_input(args.target, args.stdin)

    try:
        rules = load_rules(args.rules) if args.rules else load_rules()
    except RuleLoadError as exc:
        print(f"error loading rules: {exc}", file=sys.stderr)
        return 2

    matches = scan(text, rules)
    _print_findings(matches)

    if any(m.severity == "HIGH" for m in matches):
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sentry", description="Detect prompt-injection and agent-manipulation patterns in text.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan text or a file for detection patterns.")
    scan_parser.add_argument("target", nargs="?", default=None, help="Text to scan, or a path to a file to scan.")
    scan_parser.add_argument("--stdin", action="store_true", help="Read the text to scan from stdin.")
    scan_parser.add_argument("--rules", default=None, help="Path to a rules JSON file (defaults to the bundled ruleset).")
    scan_parser.set_defaults(func=cmd_scan)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
