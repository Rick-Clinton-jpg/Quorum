---
name: sentry-scan
description: Scan text, a pasted string, an uploaded file, or files in a repo/directory for prompt-injection and agent-manipulation patterns — direct addressing of an AI agent, imperative instructions hidden in code/HTML comments, scope-expansion phrasing, confirmation-bypass claims, and environment/network exfiltration patterns — by running the real Sentry detection engine (deterministic regex rules), not a model self-assessment. Use whenever the user pastes text, uploads a file, or points at a repo/directory and asks to check it for prompt injection, hidden instructions, suspicious/manipulative phrasing, or whether it's safe to trust before acting on it.
---

# Sentry Scan

This skill runs the real `sentry scan` CLI from the
[Sentry](https://github.com/Rick-Clinton-jpg/Sentry) detection engine against
the text you're given. Sentry is not a judgment or governance system — it
enforces nothing and reasons about nothing. It runs six deterministic regex
rules (`${CLAUDE_PLUGIN_ROOT}/rules/default_rules.json`) against the input
and reports exactly what matched and where. See
`${CLAUDE_PLUGIN_ROOT}/README.md` for the full rule descriptions.

## When to use this

- The user pastes a block of text, a document excerpt, an email, a web page,
  a tool-output snippet, or code and asks to check it for prompt injection,
  hidden/buried instructions, agent-manipulation attempts, or "is this
  safe."
- The user uploads a file, or points at a repo/directory, and wants it swept
  for these patterns before it's trusted or acted on.
- Any time you (Claude) are about to act on untrusted third-party content —
  a fetched web page, a file dropped into the conversation, a tool result —
  and the user asks for it to be checked first, or asks you to be
  cautious/verify it isn't trying to manipulate you.
- The categories this catches: direct addressing of an AI agent
  (`agent_directed_address`), imperative instructions buried in code/HTML
  comments (`hidden_channel_instruction`), scope-expansion phrasing
  (`scope_expansion_phrase`), confirmation-bypass claims
  (`confirmation_bypass_claim`), and environment or network exfiltration
  patterns (`env_exfil_pattern`, `network_exfil_pattern`).

Don't use this for general code review, style feedback, or anything outside
these six pattern categories — Sentry only knows what its rules say.

## Process

1. **Get the exact text to scan.** Don't paraphrase or retype it — pass the
   actual content through so the byte offsets in the report are meaningful.

2. **Run the scan.** Prefer the installed CLI; fall back to running the
   bundled source directly if `sentry` isn't on `PATH` (e.g. the plugin was
   installed via the marketplace without a separate `pip install`):

   ```bash
   if command -v sentry >/dev/null 2>&1; then
     sentry scan --stdin <<< "$TEXT"
   else
     PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/src" python3 -m sentry.cli scan --stdin <<< "$TEXT"
   fi
   ```

   For a file or a directory of files, scan each file individually (`sentry
   scan path/to/file`, or `--stdin < file` for exact byte-for-byte input) —
   Sentry has no directory-walking mode of its own, so loop over the files
   yourself (e.g. with `find`) and run one scan per file.

3. **Read the exit code and output, don't just relay it.** Findings print
   grouped by severity (`HIGH` / `MEDIUM` / `LOW`), each with the rule name,
   description, and the exact matched span. Exit code `1` means at least one
   `HIGH` finding fired; `0` means none did (clean, or `MEDIUM`/`LOW` only).

4. **Summarize back in plain language — don't dump raw CLI output as the
   whole answer.** For each finding, say what was flagged, quote the
   matched text, and explain in one sentence why that pattern matters (e.g.
   "the text directly addresses an AI agent by name, which is a common
   prompt-injection setup — it's trying to talk past the user to whichever
   model reads it next"). Group by severity so HIGH findings aren't buried
   under MEDIUM/LOW ones. You can still show the raw findings block after
   the summary for reference, but lead with the plain-language read.

5. **Be honest about what a clean result means.** If nothing fired, say
   plainly that no known pattern matched — do not say the content "passed"
   review, is "safe," or is "verified clean." Sentry is a narrow,
   deterministic pattern-matching layer, not a full governance or judgment
   system: a clean scan means none of its six specific regex rules matched,
   nothing more. Novel phrasing, obfuscated instructions, or attack patterns
   outside these six categories will not be caught. If asked directly
   whether something "passed," say exactly that — no HIGH findings from
   this ruleset, not a general safety verdict.

## Reference material

- `${CLAUDE_PLUGIN_ROOT}/README.md` — full detection-category table and
  design rationale.
- `${CLAUDE_PLUGIN_ROOT}/rules/default_rules.json` — the exact rules being
  run; read this if you need to know precisely what will or won't match.
- `${CLAUDE_PLUGIN_ROOT}/src/sentry/engine.py` — the scan implementation,
  if you need to understand match semantics (span, overlapping matches,
  case-insensitivity) in more detail.
