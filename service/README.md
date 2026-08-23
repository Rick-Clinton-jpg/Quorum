# service/ — Quorum coordinator HTTP service

Wraps the gate (`gate/quorum_gate.py`) behind two endpoints. `/status` is
deliberately not called `/healthz` - confirmed live against a deployed
Cloud Run service that Google's platform layer intercepts that exact
path on `*.run.app` before it reaches the container (a 404 with no
`server: Google Frontend` / `x-cloud-trace-context` headers, unlike every
other route on the same container - i.e. a path collision with reserved
platform infrastructure, not an app bug).

Runs fully locally with zero GCP setup — Firestore-backed state
(`gate/firestore_audit.py`, `gate/firestore_intent.py`) falls back to
local storage automatically when Firestore isn't configured.

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

**1. One-time setup.** `gcloud run deploy --source` builds via Cloud
Build, which runs as the Compute Engine default service account
(`<PROJECT_NUMBER>-compute@developer.gserviceaccount.com`) unless told
otherwise. Confirmed live: skipping any of these roles fails the deploy
itself (not just the first real request) — `storage.objectViewer` is
required to read the uploaded source zip, `artifactregistry.writer` to
push the built image. `aiplatform.user` is what the *running* service
needs at request time to call Vertex AI, not the build step.

```bash
PROJECT_ID=<PROJECT_ID>
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')

gcloud services enable run.googleapis.com aiplatform.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com \
  cloudresourcemanager.googleapis.com --project=$PROJECT_ID

for ROLE in roles/aiplatform.user roles/storage.objectViewer roles/artifactregistry.writer roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="$ROLE"
done
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
curl "$SERVICE_URL/status"
```

Note: `/status`, not `/healthz` — confirmed live that Google's platform
layer intercepts the literal path `/healthz` on `*.run.app` before it
reaches the container (404, missing the `server: Google Frontend` /
`x-cloud-trace-context` headers every real container-forwarded response
carries), regardless of what the app itself does with that route.
