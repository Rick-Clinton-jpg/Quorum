# Cloud Run image for service/main.py. Not deployed yet - see
# service/README.md for what's still needed before `gcloud run deploy`
# actually works (IAM, real credentials at deploy time, a timeout bump).

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

# python:3.11-slim has no git - gate/github_action.py's clone/branch/
# apply/commit/push (Phase 2's PR-opening action) shells out to the real
# git binary, not a Python git library. Confirmed live: without this,
# every PASS verdict's action step fails with "No such file or
# directory: 'git'".
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

COPY service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Vendored verifier source (verifiers/, gate/, worker_agent/) has to come
# with the image - none of it is pip-installed, it's imported straight
# off disk via gate/quorum_paths.py's sys.path setup. .dockerignore keeps
# .venv/.env/.git out of this.
COPY . .

RUN useradd -m -u 1000 appuser \
    && mkdir -p /app/.quorum/audit \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

CMD ["sh", "-c", "uvicorn service.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
