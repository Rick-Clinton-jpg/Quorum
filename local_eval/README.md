# local_eval/ — LM Studio test harness

Runs the real Quorum pipeline against a model in LM Studio instead of
Gemini, for iterating on the gate/self-check/wiring without spending
Gemini's rate-limited quota. Not part of the submitted project — nothing
in `worker_agent/` or `gate/` imports from here.

Full explanation of what it does, what it fetches and from where, and
what each test checks is in the module docstring at the top of
`eval_local_llm.py` — read that before running it.

## Quick start

```bash
source worker_agent/.venv/bin/activate
pip install -r local_eval/requirements.txt
# also make sure worker_agent/requirements.txt and
# gate/requirements-quorum.txt are installed if you haven't already

# In LM Studio: load a tool-calling-capable model, start the local server

python local_eval/eval_local_llm.py
```
