# service/ — Quorum coordinator HTTP service

Wraps the gate (`gate/quorum_gate.py`) behind two endpoints. Runs fully
locally with zero GCP setup — Firestore-backed state
(`gate/firestore_audit.py`, `gate/firestore_intent.py`) falls back to
local storage automatically when Firestore isn't configured.

**Not deployed.** That's deliberate — deployment is a separate, later
step, once real GCP credentials are available. Everything in this
directory can be built and tested without them.

## Run locally

```bash
source worker_agent/.venv/bin/activate
pip install -r service/requirements.txt
uvicorn service.main:app --reload --port 8080
```

```bash
curl -X POST localhost:8080/gate/run \
  -H "Content-Type: application/json" \
  -d @gate/tests/fixtures/markdown_exfil_proposal.json  # wrap under {"proposal": ..., "session_id": "test"}
```

## Deploy (once credentials are connected)

```bash
gcloud run deploy quorum-coordinator \
  --source . \
  --region us-central1 \
  --timeout 900 \
  --set-env-vars GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=<PROJECT_ID>,GOOGLE_CLOUD_LOCATION=us-central1
```

Two things to do once, before the first deploy — neither happens
automatically:

- Grant the Cloud Run service account `roles/aiplatform.user` (or
  equivalent) so it can call Vertex AI.
- Decide on auth (`--allow-unauthenticated` exposes the endpoint, and any
  hit against it spends real Vertex AI quota — make that call
  deliberately, not by default).
