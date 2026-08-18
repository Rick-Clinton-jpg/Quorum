"""
run_real_eval.py — schema-enforced structured-output version.

WHY THIS VERSION EXISTS
------------------------
The first real cross-model run (Claude Sonnet 4.6, Gemini 3.6 Flash) showed
each model inventing its own edge-type vocabulary for the same relationship:
Claude used CONTRADICTS, Gemini used REFUTES. Claude used NEUTRAL for "doesn't
resolve it," Gemini used NEUTRAL in one case and INSUFFICIENT_EVIDENCE in
another. None of that was a model failure — the original prompt only showed
one example edge type (SUPPORTS) and never constrained the vocabulary, so
each model reasonably filled the gap with its own word choice.

This version fixes the cause, not the symptom. Instead of asking nicely and
patching drift afterward, it uses each provider's schema-enforced output mode
— a JSON Schema compiled into a generation-time grammar, so the model is
structurally unable to produce a value outside the enum you define.
"SUPPORTS" | "CONTRADICTS" | "INCONCLUSIVE" are the only legal edge types now,
for every provider that supports it — not because the prompt asked nicely,
but because nothing else is a reachable token sequence.

  Claude   -> Structured Outputs beta (output_format, json_schema)
              header: anthropic-beta: structured-outputs-2025-11-13
  Groq     -> response_format json_schema (model-dependent support)
  Gemini   -> response_schema in generation_config
  GitHub   -> response_format json_schema (OpenAI-compatible, model-dependent)

HONEST STATUS
--------------
This has NOT been run against live APIs from where it was written — that
sandbox has no network access. The Claude REST shape and Groq/Gemini SDK
parameters are transcribed directly from each provider's current docs, but
first real use may surface a parameter name or envelope quirk I couldn't
verify without a live call. That's what the fallback path is for: every
provider function tries schema-enforced mode first, and on any failure
(unsupported model, malformed schema, wrong param name) falls back to the
old plain-prompt approach and marks that row "schema_enforced": false in the
output, rather than crashing the run. Check that field before trusting a
batch's edge-type cleanliness.

SETUP
-----
    pip install openai requests           # Claude, Groq, GitHub
    pip install google-generativeai        # Gemini, if you get quota back

USAGE
-----
    python run_real_eval.py --model claude-sonnet-4-6 --batch ground_truth
    python run_real_eval.py --model groq-llama-3.3-70b-versatile --batch ground_truth
    python run_real_eval.py --model claude-sonnet-4-6 --batch opinion --n 50

Then, same as before:
    python score_ground_truth.py graphs_ground_truth_claude-sonnet-4-6.jsonl
    python eval_harness.py graphs_opinion_claude-sonnet-4-6.jsonl
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

from ground_truth_cases import CASES as GROUND_TRUTH_CASES

# ── SCHEMAS — this is the actual fix ────────────────────────────────
#
# Every optional field is typed nullable and listed in "required" per node.
# That's not decoration — strict schema mode on Claude/OpenAI-compatible
# providers requires every property to be required when additionalProperties
# is false; nullable-and-required is the documented way to express "this
# field doesn't always apply." A model must emit null for it, not omit it.

GROUND_TRUTH_SCHEMA = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string", "enum": ["FACT", "UNKNOWN", "CONCLUSION"]},
                    "label": {"type": "string"},
                    "origin": {"type": ["string", "null"], "enum": ["RETRIEVED", "GENERATED", None]},
                    "source": {"type": ["string", "null"]},
                    "confidence": {"type": ["number", "null"]},
                    "derivation": {"type": ["string", "null"], "enum": ["EVIDENCE", "REASONING", None]},
                },
                "required": ["id", "type", "label", "origin", "source", "confidence", "derivation"],
                "additionalProperties": False,
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "array",
                "prefixItems": [
                    {"type": "string"},
                    {"type": "string"},
                    {"type": "string", "enum": ["SUPPORTS", "CONTRADICTS", "INCONCLUSIVE"]},
                ],
                "items": False,
                "minItems": 3,
                "maxItems": 3,
            },
        },
    },
    "required": ["nodes", "edges"],
    "additionalProperties": False,
}

OPINION_SCHEMA = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string",
                             "enum": ["FACT", "ASSUMPTION", "UNKNOWN", "CONCLUSION"]},
                    "label": {"type": "string"},
                    "origin": {"type": ["string", "null"],
                               "enum": ["RETRIEVED", "USER_INPUT", "VERIFIED",
                                        "INFERRED", "REPORTED", "GENERATED", None]},
                    "source": {"type": ["string", "null"]},
                    "confidence": {"type": ["number", "null"]},
                    "derivation": {"type": ["string", "null"],
                                   "enum": ["EVIDENCE", "REASONING", "VERIFICATION", None]},
                },
                "required": ["id", "type", "label", "origin", "source", "confidence", "derivation"],
                "additionalProperties": False,
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "array",
                "prefixItems": [
                    {"type": "string"},
                    {"type": "string"},
                    {"type": "string",
                     "enum": ["SUPPORTS", "CONTRADICTS", "DEPENDS_ON",
                              "RELATES_TO", "INCONCLUSIVE"]},
                ],
                "items": False,
                "minItems": 3,
                "maxItems": 3,
            },
        },
    },
    "required": ["nodes", "edges"],
    "additionalProperties": False,
}

# ── PROMPTS — unchanged in content, schema does the enforcing now ───

OPINION_PROMPT = """Analyse the question below and output your reasoning as a JSON graph
matching the schema you were given.

Rules:
- origin RETRIEVED only if the fact came from a document you were given.
- origin GENERATED if it came from your own knowledge.
- UNKNOWN nodes carry no confidence (use null).
- Link every conclusion to what supports it.

Question: {question}"""

GROUND_TRUTH_PROMPT = """Read the document below, then evaluate the claim that follows. Output
your reasoning as a JSON graph matching the schema you were given.

DOCUMENT:
{document}

CLAIM: {claim}

Rules:
- origin RETRIEVED means the fact's content appears in the DOCUMENT above.
  Do NOT mark something RETRIEVED if it is not in the document, even if you
  believe it's true from general knowledge — the document is a closed world.
- origin GENERATED means the claim came from your own knowledge, not the
  document.
- If the document does not address the claim, use an UNKNOWN node instead of
  guessing, and keep the CONCLUSION confidence near 0.5 rather than
  committing to a side.
- CONCLUSION confidence represents how strongly the document supports the
  claim being TRUE: 1.0 = fully supported, 0.0 = fully contradicted, 0.5 =
  not addressed.
- Use INCONCLUSIVE for an edge that acknowledges evidence without resolving
  the claim either way — that is a real, distinct relation, not an absence
  of one."""

QUESTIONS = [
    "Should I invest in cryptocurrency given current market conditions?",
    "Is it safe to quit my job without another offer lined up?",
    "Should a small business adopt AI customer service?",
    "Is remote work more productive than office work?",
    "Should cities ban personal cars to reduce pollution?",
    "Is universal basic income economically viable?",
    "Should social media platforms be regulated for misinformation?",
    "Is nuclear energy the best solution for climate change?",
    "Should companies mandate return-to-office policies?",
    "Is space exploration worth the current investment?",
    "Should governments implement carbon taxes?",
    "Is online education as effective as in-person learning?",
    "Should healthcare be fully privatized?",
    "Is artificial general intelligence achievable by 2030?",
    "Should countries adopt a four-day work week?",
    "Is veganism the most sustainable diet?",
    "Should encryption have government backdoors?",
    "Is automation a net positive for employment?",
    "Should water be treated as a commodity?",
    "Is democracy the best form of government?",
    "Should genetic engineering in humans be allowed?",
    "Is renewable energy sufficient to replace fossil fuels?",
    "Should minimum wage be doubled?",
    "Is artificial meat viable at scale?",
    "Should college education be free?",
    "Is facial recognition technology ethical for public use?",
    "Should countries close borders to immigration?",
    "Is blockchain technology useful beyond cryptocurrency?",
    "Should companies be allowed to mine personal data?",
    "Is Mars colonization feasible within 50 years?",
    "Should drug patents be abolished?",
    "Is public transport better than electric cars?",
    "Should AI be allowed to make medical diagnoses?",
    "Is deforestation reversible?",
    "Should voting be mandatory?",
    "Is telemedicine as good as in-person care?",
    "Should there be a global minimum tax on corporations?",
    "Is vertical farming the future of agriculture?",
    "Should police use predictive policing algorithms?",
    "Is fusion energy practically achievable?",
    "Should languages preserve dying indigenous tongues?",
    "Is open-source software more secure?",
    "Should there be limits on campaign spending?",
    "Is 3D printing viable for construction?",
    "Should animals have legal rights?",
    "Is quantum computing overhyped?",
    "Should there be a wealth tax?",
    "Is peer-to-peer lending safer than banks?",
    "Should autonomous weapons be banned?",
    "Is ocean cleanup technology effective?",
]

# ── API CLIENTS — each tries schema-enforced mode, falls back on failure ──
# Every call_* function returns (raw_text, schema_enforced: bool).

def call_claude(api_key, model, prompt, schema):
    import requests
    base_headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    strict_headers = dict(base_headers, **{"anthropic-beta": "structured-outputs-2025-11-13"})
    body = {
        "model": model, "max_tokens": 800, "temperature": 0.3,
        "messages": [{"role": "user", "content": prompt}],
        "output_format": {"type": "json_schema",
                           "json_schema": {"name": "reasoning_graph", "schema": schema}},
    }
    resp = requests.post("https://api.anthropic.com/v1/messages",
                         headers=strict_headers, json=body, timeout=60)
    if resp.status_code == 200:
        data = resp.json()
        text = "".join(b["text"] for b in data["content"] if b.get("type") == "text")
        return text, True

    # fallback: plain prompt, no schema
    plain_body = {"model": model, "max_tokens": 800, "temperature": 0.3,
                  "messages": [{"role": "user", "content": prompt}]}
    resp2 = requests.post("https://api.anthropic.com/v1/messages",
                          headers=base_headers, json=plain_body, timeout=60)
    resp2.raise_for_status()
    data = resp2.json()
    text = "".join(b["text"] for b in data["content"] if b.get("type") == "text")
    return text, False


def call_groq(api_key, model, prompt, schema):
    try:
        from openai import OpenAI
    except ImportError:
        print("ERROR: pip install openai")
        sys.exit(1)
    client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    try:
        resp = client.chat.completions.create(
            model=model, temperature=0.3, max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "reasoning_graph", "strict": True,
                                             "schema": schema}},
        )
        return resp.choices[0].message.content, True
    except Exception as e:
        print(f"  [structured output not supported for {model}: {e}; falling back]")
        resp = client.chat.completions.create(
            model=model, temperature=0.3, max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content, False


def call_google(api_key, model, prompt, schema):
    try:
        import google.generativeai as genai
    except ImportError:
        print("ERROR: pip install google-generativeai")
        sys.exit(1)
    genai.configure(api_key=api_key)
    m = genai.GenerativeModel(model)
    try:
        resp = m.generate_content(
            prompt,
            generation_config={"temperature": 0.3, "max_output_tokens": 800,
                               "response_mime_type": "application/json",
                               "response_schema": schema},
        )
        return resp.text, True
    except Exception as e:
        print(f"  [structured output not supported for {model}: {e}; falling back]")
        resp = m.generate_content(prompt, generation_config={"temperature": 0.3,
                                                              "max_output_tokens": 800})
        return resp.text, False


def call_github(token, model, prompt, schema):
    try:
        from openai import OpenAI
    except ImportError:
        print("ERROR: pip install openai")
        sys.exit(1)
    client = OpenAI(base_url="https://models.inference.ai.azure.com", api_key=token)
    try:
        resp = client.chat.completions.create(
            model=model, temperature=0.3, max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "reasoning_graph", "strict": True,
                                             "schema": schema}},
        )
        return resp.choices[0].message.content, True
    except Exception as e:
        print(f"  [structured output not supported for {model}: {e}; falling back]")
        resp = client.chat.completions.create(
            model=model, temperature=0.3, max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content, False


def extract_json(text):
    """Only matters on the fallback path now — schema-enforced responses
    don't need markdown-fence stripping, they're already bare JSON."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


# ── MAIN ────────────────────────────────────────────────────────────

def resolve_caller(model_id):
    if model_id.startswith("claude-"):
        provider = "anthropic"
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        call_fn = lambda p, s: call_claude(api_key, model_id, p, s)
    elif model_id.startswith("groq-"):
        provider = "groq"
        api_key = os.environ.get("GROQ_API_KEY")
        actual_model = model_id.replace("groq-", "")
        call_fn = lambda p, s: call_groq(api_key, actual_model, p, s)
    elif model_id.startswith("google-"):
        provider = "google"
        api_key = os.environ.get("GOOGLE_API_KEY")
        actual_model = model_id.replace("google-", "")
        call_fn = lambda p, s: call_google(api_key, actual_model, p, s)
    elif model_id.startswith("github-"):
        provider = "github"
        api_key = os.environ.get("GITHUB_TOKEN")
        actual_model = model_id.replace("github-", "")
        call_fn = lambda p, s: call_github(api_key, actual_model, p, s)
    else:
        print(f"Unknown model format: {model_id}")
        print("Use: claude-sonnet-4-6, groq-llama-3.3-70b-versatile, "
              "google-gemini-2.5-flash, github-gpt-4o")
        sys.exit(1)

    if not api_key:
        print(f"ERROR: Set {provider.upper()}_API_KEY environment variable")
        print(f"  export {provider.upper()}_API_KEY=your_key_here")
        sys.exit(1)
    return call_fn


def run_opinion(model_id, n, out_file, call_fn):
    n = min(n, len(QUESTIONS))
    print(f"Running {n} OPINION questions on {model_id} (schema-enforced, "
          f"behavior-only, no ground truth)")
    print(f"Output: {out_file}\n{'-'*50}")

    results = []
    for i, question in enumerate(QUESTIONS[:n]):
        prompt = OPINION_PROMPT.format(question=question)
        print(f"[{i+1}/{n}] {question[:50]}...", end=" ")
        try:
            raw, enforced = call_fn(prompt, OPINION_SCHEMA)
            data = json.loads(extract_json(raw))
            if "nodes" not in data or "edges" not in data:
                raise ValueError("Missing nodes or edges")
            data["id"] = f"{model_id}-op-{i:03d}"
            data["source"] = model_id
            data["batch"] = "opinion"
            data["schema_enforced"] = enforced
            data["question"] = question
            data["timestamp"] = datetime.now().isoformat()
            results.append(data)
            print("done" if enforced else "done (fallback, unenforced)")
        except Exception as e:
            print(f"failed ({e})")
        time.sleep(2)

    with open(out_file, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    enforced_count = sum(1 for r in results if r.get("schema_enforced"))
    print(f"{'-'*50}\nDone. {len(results)}/{n} successful, "
          f"{enforced_count} schema-enforced. Saved to {out_file}")
    print(f"\nThis batch has NO ground truth. Run:\n  python eval_harness.py {out_file}")


def run_ground_truth(model_id, n, out_file, call_fn):
    cases = GROUND_TRUTH_CASES[:min(n, len(GROUND_TRUTH_CASES))]
    print(f"Running {len(cases)} GROUND-TRUTH cases on {model_id} (schema-enforced)")
    print(f"Output: {out_file}\n{'-'*50}")

    results = []
    for i, case in enumerate(cases):
        prompt = GROUND_TRUTH_PROMPT.format(document=case["document"], claim=case["claim"])
        print(f"[{i+1}/{len(cases)}] {case['id']} ({case['ground_truth']})...", end=" ")
        try:
            raw, enforced = call_fn(prompt, GROUND_TRUTH_SCHEMA)
            data = json.loads(extract_json(raw))
            if "nodes" not in data or "edges" not in data:
                raise ValueError("Missing nodes or edges")
            data["id"] = case["id"]
            data["source"] = model_id
            data["batch"] = "ground_truth"
            data["schema_enforced"] = enforced
            data["ground_truth"] = case["ground_truth"]
            data["document"] = case["document"]
            data["claim"] = case["claim"]
            data["timestamp"] = datetime.now().isoformat()
            results.append(data)
            print("done" if enforced else "done (fallback, unenforced)")
        except Exception as e:
            print(f"failed ({e})")
        time.sleep(2)

    with open(out_file, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    enforced_count = sum(1 for r in results if r.get("schema_enforced"))
    print(f"{'-'*50}\nDone. {len(results)}/{len(cases)} successful, "
          f"{enforced_count} schema-enforced. Saved to {out_file}")
    if enforced_count < len(results):
        print(f"{len(results) - enforced_count} case(s) fell back to unenforced — "
              f"check those rows' edge types by hand before trusting the batch.")
    print(f"\nRun:\n  python score_ground_truth.py {out_file}")


def main():
    parser = argparse.ArgumentParser(description="Generate real model eval data, schema-enforced")
    parser.add_argument("--model", required=True,
                        help="e.g. claude-sonnet-4-6, groq-llama-3.3-70b-versatile")
    parser.add_argument("--batch", required=True, choices=["opinion", "ground_truth"])
    parser.add_argument("--n", type=int, default=50, help="Number of cases (default: all)")
    parser.add_argument("--out", help="Output file (default: auto-named per batch+model)")
    args = parser.parse_args()

    call_fn = resolve_caller(args.model)
    out_file = args.out or f"graphs_{args.batch}_{args.model}.jsonl"

    if args.batch == "ground_truth":
        run_ground_truth(args.model, args.n, out_file, call_fn)
    else:
        run_opinion(args.model, args.n, out_file, call_fn)


if __name__ == "__main__":
    main()
