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
needs at request time to call Vertex AI, not the build step;
`cloudtrace.agent` is what it needs to export the OpenTelemetry spans
(`gate/otel_tracing.py`) to Cloud Trace instead of silently falling back
to console output; `secretmanager.secretAccessor` is what it needs to
read the GitHub PR-opening token from Secret Manager (below), instead of
that token sitting in a plain environment variable.

```bash
PROJECT_ID=<PROJECT_ID>
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')

gcloud services enable run.googleapis.com aiplatform.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com \
  cloudresourcemanager.googleapis.com firestore.googleapis.com \
  cloudtrace.googleapis.com secretmanager.googleapis.com --project=$PROJECT_ID

for ROLE in roles/aiplatform.user roles/storage.objectViewer roles/artifactregistry.writer roles/logging.logWriter roles/datastore.user roles/cloudtrace.agent; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="$ROLE"
done
```

If a Firestore database doesn't exist in the project yet, create one
(Native mode) before deploying:

```bash
gcloud firestore databases create --location=us-central1 --project=$PROJECT_ID
```

Confirmed live: `read()`'s query (`gate/firestore_audit.py`) filters by
`agent_id` and orders by `timestamp` — a compound query Firestore
requires a composite index for. Without it, every read silently falls
back to the Cloud Run container's own ephemeral local disk instead of
Firestore, with no error surfaced to the API caller — it only shows up
as `"Firestore read failed... reading from local fallback"` in Cloud Run
logs. Create the index once, from the Firestore Console → Indexes → Create
index (or trigger the auto-generated link the same error gives you on
first read): collection `quorum_audit_logs`, fields `agent_id`
(Ascending) + `timestamp` (Descending), scope Collection.

**2. GitHub PR-opening token, via Secret Manager, not a plain env var.**
If you want the gate to actually open a real pull request on a PASS
verdict (`gate/github_action.py`), create the token as a secret instead
of a `--set-env-vars` value — the interactive `--data-file=-` form below
never puts the token in shell history, a file, or a chat transcript:

```bash
gcloud secrets create quorum-github-token --data-file=- --project=$PROJECT_ID
# paste the token, then Ctrl+D

gcloud secrets add-iam-policy-binding quorum-github-token \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor" --project=$PROJECT_ID
```

**3. Agent Identity — per-agent API keys, optional.** `gate/agent_identity.py`
gates `POST /gate/run` and `POST /gate/retry` behind an `X-Quorum-Agent-Key`
header, resolved to a real `agent_id` that then appears in every audit log
entry that request produces — replacing what used to be one hardcoded
`agent_id="quorum-worker-agent"` string regardless of caller. Read-only
endpoints (`/`, `/api`, `/status`, `/audit/trail`) are never gated, so a
judge or reviewer can still load the service and browse the audit trail
with no key. If `QUORUM_AGENT_KEYS` is unset entirely, auth is off — this
is what local dev and the test suite run with by default. To turn it on,
set it to a JSON object mapping each issued key to the agent_id it should
resolve to:

```bash
--set-env-vars QUORUM_AGENT_KEYS='{"qk_live_<random>":"quorum-worker-agent-prod"}'
```

(Generate `<random>` with e.g. `openssl rand -hex 24` — this is a per-agent
identity token, not a shared deploy-time secret, so a plain env var is the
right shape here; it isn't Secret-Manager-backed the way the GitHub PR
token above is, since the whole point is to issue and rotate several of
these independently, per caller.)

**4. Deploy:**

```bash
gcloud run deploy quorum-coordinator \
  --source . \
  --project $PROJECT_ID \
  --region us-central1 \
  --timeout 900 \
  --set-env-vars GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=global,QUORUM_ACTION_GITHUB_REPO=<owner>/<repo>,QUORUM_ACTION_BASE_BRANCH=<branch>,QUORUM_ACTION_TARGET_SUBDIR=verifiers/sentry,QUORUM_AGENT_KEYS='{"qk_live_<random>":"quorum-worker-agent-prod"}' \
  --update-secrets QUORUM_ACTION_GITHUB_TOKEN=quorum-github-token:latest
```

Omit `QUORUM_AGENT_KEYS` from `--set-env-vars` to deploy with agent auth
off (every caller logged as the default `quorum-worker-agent`) — everything
else in this command is unaffected either way.

`GOOGLE_CLOUD_LOCATION=global`, not a region: confirmed live that
`gemini-3.5-flash` is served from Vertex AI's `global` endpoint in this
project, not `us-central1` — the regional endpoint 404s with "Publisher
model ... was not found," even though the model is GA and reachable at
the unversioned global one.

You'll be prompted for `--allow-unauthenticated` interactively if you
don't pass it — decide deliberately: an unauthenticated public endpoint
means anyone who finds the URL can spend Vertex AI credit on every hit,
and any session_id is readable/writable by anyone with the URL (no
per-caller auth exists yet). For a hackathon demo,
`--allow-unauthenticated` is probably the right call (judges need to
reach it) — but it's a decision, not a default.

**4. Confirm it's live:**

```bash
SERVICE_URL=$(gcloud run services describe quorum-coordinator --region us-central1 --format='value(status.url)')
curl "$SERVICE_URL/status"
```

Note: `/status`, not `/healthz` — confirmed live that Google's platform
layer intercepts the literal path `/healthz` on `*.run.app` before it
reaches the container (404, missing the `server: Google Frontend` /
`x-cloud-trace-context` headers every real container-forwarded response
carries), regardless of what the app itself does with that route.
