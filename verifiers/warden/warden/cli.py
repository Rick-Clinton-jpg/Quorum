"""Click-based CLI for WARDEN."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click

from . import __version__
from .core import Warden


def _parse_since(value: str) -> datetime:
    """Parse relative times like '1 hour ago', '30m', '2h', or ISO."""
    value = value.strip().lower()
    now = datetime.now().astimezone()

    if value.endswith("ago"):
        value = value[: -len("ago")].strip()

    units = {
        "s": "seconds",
        "sec": "seconds",
        "secs": "seconds",
        "second": "seconds",
        "seconds": "seconds",
        "m": "minutes",
        "min": "minutes",
        "mins": "minutes",
        "minute": "minutes",
        "minutes": "minutes",
        "h": "hours",
        "hr": "hours",
        "hrs": "hours",
        "hour": "hours",
        "hours": "hours",
        "d": "days",
        "day": "days",
        "days": "days",
    }

    import re

    m = re.match(r"^(\d+)\s*([a-z]+)?$", value)
    if m:
        num = int(m.group(1))
        unit = units.get(m.group(2) or "m", "minutes")
        return now - timedelta(**{unit: num})

    # Try ISO
    try:
        return datetime.fromisoformat(value)
    except Exception:
        raise click.BadParameter(f"Cannot parse time: {value}")


@click.group()
@click.version_option(__version__, prog_name="warden")
@click.option(
    "--root",
    default=".warden",
    show_default=True,
    help="Runtime data directory",
)
@click.pass_context
def cli(ctx: click.Context, root: str) -> None:
    """WARDEN — Lightweight agent drift detection & audit."""
    ctx.ensure_object(dict)
    ctx.obj["warden"] = Warden(root=root)


@cli.command("watch")
@click.option("--agent", "agent_type", default="claude-code", show_default=True,
              help="Agent type / polling strategy (claude-code, codex, manual, auto)")
@click.option("--session", "session_id", required=True, help="Session identifier")
@click.option("--objective", required=True, help="Stated objective for this session")
@click.option("--interval", default=600, show_default=True, help="Poll interval in seconds")
@click.option("--transcript", default=None, help="Explicit path to session transcript / log")
@click.option("--once", is_flag=True, help="Register, run a single check, and exit")
@click.option("--follow", is_flag=True,
              help="Register, then block and poll this session on --interval until Ctrl-C")
@click.option("--background", is_flag=True,
              help="Deprecated: registration is the default now, this flag is a no-op")
@click.pass_context
def watch_cmd(
    ctx: click.Context,
    agent_type: str,
    session_id: str,
    objective: str,
    interval: int,
    transcript: str | None,
    once: bool,
    follow: bool,
    background: bool,
) -> None:
    """Register a session for monitoring against an objective.

    By default this only registers the session — use `warden daemon` to
    actively check every registered session on its own interval, or
    `--follow` to block and poll just this one session in the foreground.
    """
    w: Warden = ctx.obj["warden"]
    session = w.watch(
        agent_id=session_id,
        objective=objective,
        agent_type=agent_type,
        interval_seconds=interval,
        transcript_path=transcript,
    )
    click.echo(f"[warden] Registered session '{session.agent_id}'")
    click.echo(f"         objective : {session.objective}")
    click.echo(f"         strategy  : {session.strategy_name}")
    click.echo(f"         interval  : {session.interval_seconds}s")

    if once:
        result = w.check_once(session_id)
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if follow:
        w.run_loop(session_id)
        return

    if background:
        click.echo(
            "[warden] Note: --background is deprecated — registration is the "
            "default now. Use 'warden daemon' to actively monitor sessions."
        )
    else:
        click.echo(
            "[warden] Registered only. Use `warden daemon` to actively monitor, "
            "or `warden check` for a one-shot poll."
        )


@cli.command("check")
@click.option("--session", "session_id", default=None, help="Check one session (default: all)")
@click.pass_context
def check_cmd(ctx: click.Context, session_id: str | None) -> None:
    """Run a one-shot status check now."""
    w: Warden = ctx.obj["warden"]
    if session_id:
        result = w.check_once(session_id)
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        results = w.check_all()
        if not results:
            click.echo("No active sessions.")
            return
        for r in results:
            tag = r.get("tag", "?")
            status = r.get("status") or "(none)"
            click.echo(f"{r['agent_id']}: {tag:7} | {status[:80]}")


@cli.command("status")
@click.pass_context
def status_cmd(ctx: click.Context) -> None:
    """Show registered sessions and last known status."""
    w: Warden = ctx.obj["warden"]
    sessions = w.list_sessions(active_only=False)
    if not sessions:
        click.echo("No sessions registered.")
        return

    for s in sessions:
        flag = "ACTIVE" if s.active else "stopped"
        click.echo(f"• {s.agent_id}  [{flag}]  ({s.agent_type})")
        click.echo(f"  objective : {s.objective[:70]}")
        if s.last_checked:
            if s.last_tag == "DIVERGENT":
                click.echo(
                    f"  last      : DIVERGENT | word={s.last_word_tag or '?'} "
                    f"trigram={s.last_trigram_tag or '?'} @ {s.last_checked}"
                )
            else:
                click.echo(
                    f"  last      : {s.last_tag or '?'} @ {s.last_checked}"
                )
            if s.last_status:
                click.echo(f"  status    : {s.last_status[:70]}")
        click.echo("")


@cli.command("audit")
@click.option("--session", default=None, help="Filter by session id")
@click.option("--since", default=None, help="e.g. '1 hour ago', '30m', ISO timestamp")
@click.option("--limit", default=50, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Raw JSON output")
@click.pass_context
def audit_cmd(
    ctx: click.Context,
    session: str | None,
    since: str | None,
    limit: int,
    as_json: bool,
) -> None:
    """View the audit trail."""
    w: Warden = ctx.obj["warden"]
    since_dt = _parse_since(since) if since else None
    records = w.audit.read(session=session, since=since_dt, limit=limit)

    if as_json:
        click.echo(json.dumps(records, indent=2, ensure_ascii=False))
        return

    if not records:
        click.echo("No audit records found.")
        return

    for r in records:
        ts = r.get("timestamp", "")
        tag = r.get("tag", "?")
        aid = r.get("agent_id", "")
        status = r.get("status") or "(none)"
        click.echo(f"[{ts}] {tag:7} {aid}")
        click.echo(f"         {status[:90]}")
        if r.get("note"):
            click.echo(f"         → {r['note'][:100]}")
        click.echo("")


@cli.command("drifts")
@click.option("--today", is_flag=True, help="Only today's records")
@click.option("--session", default=None)
@click.option("--tag", "tag", default="DRIFT", show_default=True,
              help="Tag to filter by (DRIFT, DIVERGENT, UNCLEAR, MATCH)")
@click.option("--limit", default=50, show_default=True)
@click.pass_context
def drifts_cmd(
    ctx: click.Context,
    today: bool,
    session: str | None,
    tag: str,
    limit: int,
) -> None:
    """List DRIFT (or other tagged) events."""
    w: Warden = ctx.obj["warden"]
    since = None
    if today:
        since = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    records = w.audit.read(session=session, tag=tag, since=since, limit=limit)

    if not records:
        click.echo(f"No {tag} events found.")
        return

    for r in records:
        click.echo(f"[{r.get('timestamp')}] {r.get('agent_id')}")
        click.echo(f"  status : {r.get('status') or '(none)'}")
        click.echo(f"  note   : {r.get('note', '')[:120]}")
        click.echo("")


@cli.command("stop")
@click.option("--session", "session_id", required=True)
@click.pass_context
def stop_cmd(ctx: click.Context, session_id: str) -> None:
    """Stop monitoring a session."""
    w: Warden = ctx.obj["warden"]
    if w.stop(session_id):
        click.echo(f"[warden] Stopped monitoring '{session_id}'")
    else:
        click.echo(f"[warden] No active session named '{session_id}'")


@cli.command("daemon")
@click.option("--tick", "tick_seconds", default=30, show_default=True,
              help="Seconds between scan passes across all sessions")
@click.pass_context
def daemon_cmd(ctx: click.Context, tick_seconds: int) -> None:
    """Continuously check every active session on its own --interval, in one process."""
    w: Warden = ctx.obj["warden"]
    w.run_daemon(tick_seconds=tick_seconds)


@cli.command("rule")
def rule_cmd() -> None:
    """Print the WARDEN_RULE block to inject into an agent context."""
    text = """[WARDEN_RULE]
Every 10 minutes, regardless of what you are doing,
output exactly one line in this format:

WARDEN_STATUS: [one-sentence description of current activity]

This line must be parseable and must describe what you are
actively working on right now, not what you plan to do next.
Do not explain. Do not elaborate. One sentence only.
[/WARDEN_RULE]"""
    click.echo(text)


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()