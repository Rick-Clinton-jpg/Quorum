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

## Deploy

Run these from a shell that has `gcloud` installed and authenticated to
your GCP project (`gcloud auth login`, then
`gcloud config set project <PROJECT_ID>`).

**1. One-time setup — grant Cloud Run's default service account access to
Vertex AI (skip this and every deploy will fail on the first real
request, not at deploy time):**

```bash
PROJECT_ID=<PROJECT_ID>
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')

gcloud services enable run.googleapis.com aiplatform.googleapis.com --project=$PROJECT_ID

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/aiplatform.user"
```

**2. Deploy:**

```bash
gcloud run deploy quorum-coordinator \
  --source . \
  --project <PROJECT_ID> \
  --region us-central1 \
  --timeout 900 \
  --set-env-vars GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=<PROJECT_ID>,GOOGLE_CLOUD_LOCATION=us-central1
```

You'll be prompted for `--allow-unauthenticated` interactively if you
don't pass it — decide deliberately: an unauthenticated public endpoint
means anyone who finds the URL can spend Vertex AI credit on every hit.
For a hackathon demo, `--allow-unauthenticated` is probably the right
call (judges need to reach it) — but it's a decision, not a default.

**3. Confirm it's live:**

```bash
SERVICE_URL=$(gcloud run services describe quorum-coordinator --region us-central1 --format='value(status.url)')
curl "$SERVICE_URL/healthz"
```
