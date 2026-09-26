"""Bounded link imports through the shared discovery worker."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.services.deduplicator import normalize_url
from app.services.firecrawl_service import document_url
from app.services.verifier import ATS_HOSTS

WORKFLOW_URL = (
    "https://api.github.com/repos/iamsonu149/internscout-ai/actions/workflows/job_search.yml/dispatches"
)
RUNS_URL = "https://github.com/iamsonu149/internscout-ai/actions/workflows/job_search.yml"


def validate_link(value, sources_path="sources.json"):
    if len(value) > 2048:
        raise ValueError("The link is too long.")
    url = normalize_url(value.strip())
    sources = json.loads(Path(sources_path).read_text(encoding="utf-8"))
    parts = urlsplit(url)
    allowed = ATS_HOSTS | set(sources.get("official_domains", {}))
    if document_url(url) or parts.hostname not in allowed:
        raise ValueError(
            "Use a supported careers posting (Greenhouse, Lever, Ashby or Workable). Documents and unverified websites are not supported."
        )
    if len(parts.path.strip("/").split("/")) < 2:
        raise ValueError("Paste an individual internship posting, not a careers homepage.")
    return url


def dispatch_import(url, request_id):
    token = os.getenv("GITHUB_IMPORT_TOKEN", "")
    if not token:
        raise RuntimeError("Link imports need the private worker connection to be configured.")
    try:
        # This workflow shares a single history cache. Do not displace a pending
        # scheduled run/import in GitHub's concurrency group.
        active = httpx.get(
            WORKFLOW_URL.replace("/dispatches", "/runs"),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            params={"per_page": 20},
            timeout=20,
            follow_redirects=False,
        )
        if active.status_code != 200:
            raise RuntimeError("Could not check the worker. Check its connection settings.")
        if any(run.get("status") != "completed" for run in active.json().get("workflow_runs", [])):
            raise RuntimeError("A search or import is already running. Please wait for it to finish.")
        response = httpx.post(
            WORKFLOW_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"ref": "main", "inputs": {"job_url": url, "request_id": request_id}},
            timeout=20,
            follow_redirects=False,
        )
    except httpx.HTTPError:
        raise RuntimeError(
            "Could not confirm submission. Check import history before trying again."
        ) from None
    if response.status_code != 204:
        raise RuntimeError("The worker did not accept this import. Check its connection settings.")
