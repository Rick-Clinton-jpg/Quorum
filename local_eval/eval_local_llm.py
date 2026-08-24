"""
================================================================================
Quorum — Local LLM Eval Harness (LM Studio)
================================================================================

RUN THIS ON YOUR OWN MACHINE, NOT IN A REMOTE SESSION.
It talks to http://localhost:1234 by default - that only means anything
on the machine actually running LM Studio.


WHAT THIS IS
------------
A test harness that runs the REAL Quorum code - worker_agent/tools.py,
worker_agent/schema.py, worker_agent/self_check.py, gate/quorum_gate.py -
against a model running locally in LM Studio, instead of Gemini.

It does not reimplement or approximate any of Quorum's logic. Every test
below imports and calls the actual functions the production path calls.
The ONLY thing that changes is which model drafts the proposal.


WHAT THIS IS **NOT**
---------------------
This is NOT an alternate way to run the real Worker Agent, and it is not
something the submitted project depends on. The hackathon's mandatory
requirement is Gemini 3.5+ via the Gemini API or Vertex AI
(see worker_agent/agent.py: MODEL = "gemini-3.5-flash") - swapping that
out for a local model would break eligibility, so nothing here is called
BY worker_agent/agent.py or gate/quorum_gate.py. This script only
imports FROM them, one direction, for local testing purposes only.

What this is actually for: iterating on everything AROUND the model -
does the gate's Sentry/Kernel/IntentGraph wiring still work, does the
self-check loop still apply and test a patch correctly, does the full
PASS/REJECT/ESCALATE pipeline hold up - without spending Gemini's
rate-limited free-tier quota (5 requests/minute, confirmed the hard way
during this project's own Phase 2/3 testing) on every single iteration.


HOW THIS CONNECTS TO LM STUDIO, EXACTLY
-----------------------------------------
LM Studio exposes an OpenAI-compatible REST API once you start its local
server: LM Studio app -> the "Developer" (or "Local Server") tab -> load
a model -> "Start Server". You should see something like
"Server running on http://localhost:1234" in the app.

This script talks to that server in exactly two ways:

  1. GET {base_url}/v1/models
     Called once, at the very start (Test 0). This is how the script
     finds out WHICH model you actually have loaded right now - it
     never assumes or hardcodes a model name, because that changes
     every time you load something different in the LM Studio UI.

  2. POST {base_url}/v1/chat/completions
     Called by `litellm` internally, every time the agent needs to
     think - this script never calls this endpoint directly. The path
     is: this script builds an ADK `LlmAgent` whose `model` is
     `google.adk.models.lite_llm.LiteLlm(model="lm_studio/<id>",
     api_base=..., api_key=...)` instead of the string
     "gemini-3.5-flash". LiteLlm is ADK's own documented mechanism for
     running any provider `litellm` supports - not a hack - and
     `lm_studio` is a provider `litellm` recognizes natively (confirmed
     by reading litellm/main.py and litellm/types/utils.py directly in
     the installed package before writing this, not assumed).

Nothing in this script ever reaches Gemini, Vertex AI, or any Google
endpoint. From Google's side, running this costs nothing and touches
nothing.

Sentry's real rules and tests are read straight off local disk from
verifiers/sentry/ (via worker_agent/tools.py, completely unmodified) -
not fetched from anywhere over the network.


BEFORE YOU RUN THIS
----------------------
  1. LM Studio running locally, a model DOWNLOADED and LOADED, and its
     local server STARTED. Confirm the "Server running on ..." message
     in the LM Studio UI before running this script - Test 0 will tell
     you clearly if it isn't reachable, but it can't tell you WHY if
     the server was simply never started.

  2. The loaded model must support TOOL / FUNCTION CALLING. This is a
     property of how the specific model was fine-tuned - not something
     this script, ADK, or litellm can add on top of a model that
     doesn't have it. Everything from Test 1 onward depends on this.
       - Likely to work: Qwen2.5-Instruct (7B+), Llama-3.1/3.2-Instruct,
         Hermes-3, Mistral-Instruct-v0.3+, any model LM Studio's own UI
         tags as supporting "Tool Use".
       - Likely to fail: a base/completion model, or anything without
         "-Instruct"/"-Chat"/"-it" in the name.
     If Test 1 fails, that is very likely the model, not this script or
     Quorum's code - try a different one and rerun before assuming
     something is broken.

  3. This repo cloned locally, with worker_agent/.venv already set up
     per worker_agent/README.md. Activate it before running this:
         source worker_agent/.venv/bin/activate

  4. One dependency the production path does NOT need, that this script
     does:
         pip install "google-adk[extensions]"
     (pulls in `litellm`; see local_eval/requirements.txt.) Everything
     else this script needs is already in worker_agent/requirements.txt
     and gate/requirements-quorum.txt.


THE TESTS
---------
Each test is self-contained, prints what it's doing as it goes, and
reports PASS/FAIL plus which layer to suspect on failure. Test N+1
generally depends on Test N having passed - the script stops and tells
you exactly where if something upstream failed, rather than continuing
into confusing downstream failures.

  Test 0 — Connectivity & model discovery
      Confirms LM Studio is reachable at all and reports which model id
      is actually loaded. Everything below uses whatever model id this
      test discovers (or --model, if you passed one explicitly).

  Test 1 — Tool-calling smoke test
      A minimal ADK agent with exactly one trivial dummy tool
      (`get_current_year()`). Asks the model a question only answerable
      by calling that tool. Confirms the loaded model can actually
      execute a function call through litellm/LM Studio at all, before
      trying anything more complex. This is the single most likely
      place for a fresh LM Studio setup to fail, and it fails in a way
      that's easy to misread as a Quorum bug if you skip straight to
      Test 2.

  Test 2 — Full Worker Agent draft
      The REAL worker_agent.agent.INSTRUCTION text, the REAL
      list_sentry_files/read_sentry_file tools (reading the REAL
      verifiers/sentry/ source on disk), and the REAL DraftProposal
      output_schema (worker_agent/schema.py) - run against the same
      markdown-image-exfiltration gap worker_agent/gap_analysis.md
      documents (or your own --task). This is a strictly higher bar
      than Test 1: the model has to call tools AND then produce
      schema-valid structured JSON, in the same turn sequence the real
      Worker Agent uses.

  Test 3 — Self-check
      The draft from Test 2, run through the REAL
      worker_agent.self_check.run_self_check() - which applies the
      draft to a throwaway temp copy of Sentry and runs Sentry's actual
      pytest suite there. Identical code path to production; only the
      draft's origin (LM Studio vs. Gemini) differs.

  Test 4 — Full gate run
      The resulting proposal, run through the REAL
      gate.quorum_gate.run_gate() - Sentry-as-verifier (scanning the
      proposal's own diff/rationale), the Reasoning Kernel (claim
      verification), and IntentGraph. Confirms the entire downstream
      chain still works regardless of which model produced the input -
      exactly the thing this script exists to let you check quickly and
      for free.

  A final summary lists PASS/FAIL for every test in one place.


USAGE
-----
    source worker_agent/.venv/bin/activate
    pip install "google-adk[extensions]"
    python local_eval/eval_local_llm.py

    # Point at a non-default LM Studio address:
    python local_eval/eval_local_llm.py --base-url http://localhost:1234/v1

    # Skip auto-discovery and force a specific loaded model id:
    python local_eval/eval_local_llm.py --model qwen2.5-7b-instruct

    # Test against a different task description than the default gap:
    python local_eval/eval_local_llm.py --task "your own task description here"
================================================================================
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import requests  # noqa: E402 - after sys.path setup, matches the rest of this repo's convention

# ---------------------------------------------------------------------------
# Pretty-printing helpers - no dependency beyond stdlib, kept simple since
# this script's job is to be read while it runs, not to look fancy.
# ---------------------------------------------------------------------------

_RESULTS: list[tuple[str, bool, str]] = []


def _section(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def _record(name: str, passed: bool, detail: str = "") -> None:
    _RESULTS.append((name, passed, detail))
    status = "PASS" if passed else "FAIL"
    print(f"\n[{status}] {name}" + (f" — {detail}" if detail else ""))


def _fail_and_exit(name: str, detail: str, suspect: str) -> None:
    _record(name, False, detail)
    print(f"    Likely cause: {suspect}")
    _print_summary()
    sys.exit(1)


def _print_summary() -> None:
    _section("SUMMARY")
    for name, passed, detail in _RESULTS:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")
    total = len(_RESULTS)
    passed_count = sum(1 for _, p, _ in _RESULTS if p)
    print(f"\n  {passed_count}/{total} tests passed.")


# ---------------------------------------------------------------------------
# Test 0 — Connectivity & model discovery
# ---------------------------------------------------------------------------


def test_0_connectivity(base_url: str, forced_model: Optional[str]) -> str:
    _section("TEST 0 — Connectivity & model discovery")
    print(f"Fetching GET {base_url}/models ...")
    try:
        resp = requests.get(f"{base_url}/models", timeout=10)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        _fail_and_exit(
            "Test 0 - Connectivity",
            f"Could not connect to {base_url}",
            "LM Studio's local server likely isn't started. Open LM Studio -> "
            "Developer/Local Server tab -> load a model -> Start Server, then rerun.",
        )
    except Exception as exc:  # noqa: BLE001
        _fail_and_exit("Test 0 - Connectivity", str(exc), "LM Studio reachable but returned an error - check its logs.")

    data = resp.json()
    models = [m["id"] for m in data.get("data", [])]
    if not models:
        _fail_and_exit(
            "Test 0 - Connectivity",
            "LM Studio reachable, but no model is loaded",
            "Load a model in LM Studio's UI before starting the server, or after - either works, but one must be loaded.",
        )

    if forced_model:
        if forced_model not in models:
            print(f"    WARNING: --model {forced_model!r} not in LM Studio's reported list {models} - trying it anyway.")
        model_id = forced_model
    else:
        model_id = models[0]
        if len(models) > 1:
            print(f"    Multiple models loaded ({models}); using the first ({model_id}). Pass --model to choose another.")

    _record("Test 0 - Connectivity", True, f"reachable, using model {model_id!r}")
    return model_id


# ---------------------------------------------------------------------------
# Shared: build a LiteLlm-backed model pointed at LM Studio
# ---------------------------------------------------------------------------


def _lm_studio_model(model_id: str, base_url: str):
    """One LiteLlm instance, reused by Tests 1 and 2. api_key is a
    placeholder string - LM Studio does not validate it by default, but
    litellm's OpenAI-compatible client requires the field to be non-empty."""
    from google.adk.models.lite_llm import LiteLlm

    return LiteLlm(model=f"lm_studio/{model_id}", api_base=base_url, api_key="lm-studio")


# ---------------------------------------------------------------------------
# Test 1 — Tool-calling smoke test
# ---------------------------------------------------------------------------


async def _run_agent_once(agent, message: str, app_name: str) -> Any:
    """Same InMemoryRunner/run_debug pattern worker_agent/orchestrator.py
    uses for the real agent - reused here so this test exercises ADK the
    same way production does."""
    from google.adk.runners import InMemoryRunner

    runner = InMemoryRunner(agent=agent, app_name=app_name)
    user_id, session_id = "local-eval", f"{app_name}-session"
    await runner.session_service.create_session(app_name=app_name, user_id=user_id, session_id=session_id)
    await runner.run_debug(message, user_id=user_id, session_id=session_id, quiet=True)
    return await runner.session_service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)


def test_1_tool_calling(model_id: str, base_url: str) -> None:
    _section("TEST 1 — Tool-calling smoke test")
    print("Building a minimal agent with one dummy tool: get_current_year().")
    print("Asking a question only answerable by calling it, and checking whether it was actually called.")

    from google.adk.agents import LlmAgent

    called = {"yes": False}

    def get_current_year() -> int:
        """Returns the current year as an integer."""
        called["yes"] = True
        return datetime.now(timezone.utc).year

    agent = LlmAgent(
        name="tool_call_smoke_test",
        model=_lm_studio_model(model_id, base_url),
        instruction="You have a tool, get_current_year(). Use it to answer the user's question. Do not guess.",
        tools=[get_current_year],
    )

    try:
        asyncio.run(_run_agent_once(agent, "What year is it right now? Use your tool, don't guess.", "tool-smoke-test"))
    except Exception as exc:  # noqa: BLE001
        _fail_and_exit(
            "Test 1 - Tool calling",
            str(exc),
            "Either LM Studio/litellm wiring is broken, or (more likely) the loaded model doesn't "
            "support tool calling at all. Try a model LM Studio itself tags as supporting 'Tool Use'.",
        )

    if not called["yes"]:
        _fail_and_exit(
            "Test 1 - Tool calling",
            "Agent responded without ever calling get_current_year()",
            "The model answered in plain text instead of calling the tool - a strong sign it doesn't "
            "reliably support function calling, even if it didn't error. Try a different model.",
        )

    _record("Test 1 - Tool calling", True, "model successfully called a real tool")


# ---------------------------------------------------------------------------
# Test 2 — Full Worker Agent draft (real instruction, real tools, real schema)
# ---------------------------------------------------------------------------

DEFAULT_TASK = """Sentry's six default rules include env_exfil_pattern and network_exfil_pattern for exfiltration via Python code (os.environ access, requests.post to a suspicious host), but none of the six rules match a well-documented alternate exfiltration channel: exfiltration via auto-rendered Markdown image syntax. Many LLM-agent front-ends and chat UIs auto-fetch image URLs found in Markdown output (e.g. ![...](https://...)), so an attacker-controlled or injected instruction can smuggle sensitive data out by asking the agent to emit a Markdown image (or plain link) whose URL embeds the data as a query parameter pointing at an attacker-controlled host - the mere act of rendering the message triggers an outbound HTTP request, with no code execution or explicit exfil pattern like os.environ/requests.post involved at all. This is a real, previously disclosed technique against LLM agents/chat UIs, and is structurally invisible to Sentry's current ruleset because none of its six rules examine Markdown link/image syntax at all. Add one new detection rule to Sentry that closes this specific gap, plus its test cases, following Sentry's existing conventions exactly."""


def test_2_full_draft(model_id: str, base_url: str, task: str) -> dict:
    _section("TEST 2 — Full Worker Agent draft")
    print("Building the REAL Worker Agent (worker_agent/agent.py's exact instruction + tools + schema),")
    print("with only the model swapped from Gemini to LM Studio.")
    print(f"\nTask description:\n  {task[:200]}{'...' if len(task) > 200 else ''}")

    import gate.quorum_paths  # noqa: F401 - sys.path side effects, needed before anything below
    from google.adk.agents import LlmAgent

    from worker_agent.agent import INSTRUCTION
    from worker_agent.schema import DraftProposal
    from worker_agent.tools import list_sentry_files, read_sentry_file

    agent = LlmAgent(
        name="quorum_worker_agent_lm_studio",
        model=_lm_studio_model(model_id, base_url),
        instruction=INSTRUCTION,
        tools=[list_sentry_files, read_sentry_file],
        output_schema=DraftProposal,
        output_key="draft",
    )

    try:
        session = asyncio.run(_run_agent_once(agent, task, "worker-agent-local-eval"))
    except Exception as exc:  # noqa: BLE001
        _fail_and_exit(
            "Test 2 - Full draft",
            str(exc),
            "Passed Test 1 but failed here - likely the model can call ONE simple tool but struggles "
            "with this agent's two-tool + structured-output-schema combination. Some smaller local "
            "models handle basic tool use but not schema-constrained output reliably.",
        )

    draft_dict = session.state.get("draft") if session else None
    if draft_dict is None:
        _fail_and_exit(
            "Test 2 - Full draft",
            "Agent ran without error but produced no structured draft",
            "The model likely never emitted a response matching DraftProposal's schema - check "
            "whatever it did say by rerunning with quiet=False in _run_agent_once for this test.",
        )

    try:
        draft = DraftProposal(**draft_dict)
    except Exception as exc:  # noqa: BLE001
        _fail_and_exit("Test 2 - Full draft", f"Draft failed schema validation: {exc}", "Model produced JSON that doesn't match DraftProposal - inspect draft_dict above.")

    print(f"\n  Drafted rule: {draft.new_rule.name!r} (severity={draft.new_rule.severity})")
    print(f"  Claims: {[c.id for c in draft.claims]}")
    _record("Test 2 - Full draft", True, f"produced a schema-valid DraftProposal ({draft.new_rule.name!r})")
    return draft_dict


# ---------------------------------------------------------------------------
# Test 3 — Self-check (real code, unmodified)
# ---------------------------------------------------------------------------


def test_3_self_check(draft_dict: dict) -> dict:
    _section("TEST 3 — Self-check")
    print("Applying the draft to a throwaway temp copy of Sentry and running Sentry's real pytest suite there -")
    print("the exact same worker_agent/self_check.py code the production path uses, unmodified.")

    from worker_agent.schema import DraftProposal
    from worker_agent.self_check import run_self_check

    draft = DraftProposal(**draft_dict)
    result, diff = run_self_check(draft, attempt=1)

    print(f"\n  existing_suite_passed={result.existing_suite_passed}  new_test_passed={result.new_test_passed}")
    if not result.passed:
        print("\n  --- pytest summary tail ---")
        print(result.pytest_summary)

    if not result.passed:
        _fail_and_exit(
            "Test 3 - Self-check",
            "Sentry's real pytest suite failed against this draft",
            "The LM-Studio-drafted rule/pattern/tests were syntactically valid JSON (Test 2 passed) "
            "but don't actually work correctly against Sentry's real code - inspect the pytest "
            "summary above. This is a legitimate quality signal about the model, not a harness bug.",
        )

    _record("Test 3 - Self-check", True, "passed Sentry's real pytest suite")
    return {"self_check_result": result.model_dump(), "diff": diff}


# ---------------------------------------------------------------------------
# Test 4 — Full gate run (real code, unmodified)
# ---------------------------------------------------------------------------


def test_4_full_gate(draft_dict: dict, task: str, self_check_bits: dict) -> None:
    _section("TEST 4 — Full gate run")
    print("Running the resulting proposal through the REAL gate.quorum_gate.run_gate() -")
    print("Sentry-as-verifier, the Reasoning Kernel, and IntentGraph. Identical to production.")

    from gate.quorum_gate import run_gate
    from intent_layer import IntentGraph

    proposal = {
        "task_description": task,
        "model": f"lm_studio/{draft_dict.get('new_rule', {}).get('name', 'unknown')}",  # informational only
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_files": draft_dict["target_files"],
        "diff": self_check_bits["diff"],
        "rationale": draft_dict["rationale"],
        "claims": draft_dict["claims"],
        "self_check_result": self_check_bits["self_check_result"],
    }

    try:
        result = run_gate(proposal, intent_graph=IntentGraph())
    except Exception as exc:  # noqa: BLE001
        _fail_and_exit("Test 4 - Full gate", str(exc), "The gate itself raised - this would also happen with a real Gemini-drafted proposal, so it's a Quorum bug, not a model quality issue. Worth reporting.")

    print(f"\n  Sentry action:   {result.sentry_action}")
    print(f"  Kernel verdict:  {result.kernel_verdict}")
    print(f"  IntentGraph risk: {result.intent_risk}")
    print(f"  Final verdict:   {result.verdict.value}")
    if result.reasons:
        print(f"  Reasons: {result.reasons}")

    _record("Test 4 - Full gate", True, f"gate returned {result.verdict.value}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:1234/v1", help="LM Studio's OpenAI-compatible base URL")
    parser.add_argument("--model", default=None, help="Force a specific loaded model id (skips auto-discovery)")
    parser.add_argument("--task", default=DEFAULT_TASK, help="Task description for Tests 2-4 (defaults to the real markdown-exfil gap)")
    args = parser.parse_args()

    model_id = test_0_connectivity(args.base_url, args.model)
    test_1_tool_calling(model_id, args.base_url)
    draft_dict = test_2_full_draft(model_id, args.base_url, args.task)
    self_check_bits = test_3_self_check(draft_dict)
    test_4_full_gate(draft_dict, args.task, self_check_bits)

    _print_summary()


if __name__ == "__main__":
    main()
