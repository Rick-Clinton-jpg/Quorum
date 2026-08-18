"""Loads worker_agent/.env (if present) as soon as this package is
imported, so GOOGLE_API_KEY/GOOGLE_GENAI_USE_VERTEXAI etc. are set before
agent.py builds anything - regardless of whether the caller is this
package's own cli.py or gate/quorum_gate.py importing
worker_agent.orchestrator from elsewhere. Does nothing if .env doesn't
exist (e.g. real Vertex AI env vars set at the process/container level
instead - see README.md)."""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
