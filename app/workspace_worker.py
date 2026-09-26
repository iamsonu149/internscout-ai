"""One-shot PostgreSQL queue consumer. Run in a dedicated background process."""

import json
import tempfile
from dataclasses import replace
from pathlib import Path

from app.database.workspace_repository import WorkspaceBudget, WorkspaceRepository
from app.services.firecrawl_service import Firecrawl
from app.services.pipeline import Pipeline
from app.services.provider_vault import ProviderVault
from app.services.workspace_profile import matching_profile
from app.services.workspace_store import WorkspaceStore


def work_once(settings, store=None):
    if not settings.supabase_worker_key or not settings.provider_encryption_key:
        raise ValueError("Worker needs its own Supabase service credential and provider encryption key.")
    store = store or WorkspaceStore(settings.supabase_url, settings.supabase_worker_key)
    if settings.workspace_worker_enabled == "on":
        store.call("POST", "/rest/v1/rpc/enqueue_due_discovery", json={})
    tasks = store.call("POST", "/rest/v1/rpc/claim_discovery", json={})
    if not tasks:
        return {"state": "IDLE"}
    task = tasks[0]
    owner = task["user_id"]
    try:
        document = store.profile(None, owner)
        profile = matching_profile(document)
        connections = store.call(
            "GET",
            "/rest/v1/provider_connections",
            params={"user_id": f"eq.{owner}", "provider": "eq.firecrawl", "select": "ciphertext"},
        )
        if not connections:
            raise ValueError("Connect your Firecrawl key before running discovery.")
        key = ProviderVault(settings.provider_encryption_key).open(
            owner, "firecrawl", connections[0]["ciphertext"]
        )
        config = store.discovery_settings(None, owner)
        budget = WorkspaceBudget(store, task, config["weekly_limit"])
        repo = WorkspaceRepository(store, task)
        with tempfile.TemporaryDirectory(prefix="internscout-worker-") as folder:
            profile_path = Path(folder) / "profile.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            runtime = replace(
                settings,
                database_path=str(Path(folder) / "unused.db"),
                profile_path=str(profile_path),
                firecrawl_api_key=key,
                sheet_id="",
                llm_api_key="",
                max_llm_calls=0,
                max_queries=3,
                max_scrapes=8,
                results_per_query=10,
                max_rechecks=4,
                min_match_score=80,
                weekly_credit_limit=config["weekly_limit"],
            )
            pipeline = Pipeline(
                runtime, firecrawl=Firecrawl(key, budget=budget), repository=repo, credit_budget=budget
            )
            if task["kind"] == "import":
                result = pipeline.import_url(task["job_url"], sync=False)
                state = "COMPLETED"
            else:
                result = pipeline.run(sync=False)
                state = result.pop("state")
    except Exception:
        # Never put credentials or raw provider errors into user-visible results.
        state, result = (
            "FAILED",
            {
                "outcome": "Could not finish discovery. Check your profile, connection and credit budget. Existing credit reservations were retained."
            },
        )
    from app.models import utcnow

    changed = store.call(
        "PATCH",
        "/rest/v1/discovery_tasks",
        params={
            "id": f"eq.{task['id']}",
            "user_id": f"eq.{owner}",
            "claim_token": f"eq.{task['claim_token']}",
            "state": "eq.RUNNING",
        },
        headers={"Prefer": "return=representation"},
        json={"state": state, "result": result, "finished_at": utcnow()},
    )
    if not changed:
        return {"state": "LEASE_LOST"}
    return {"state": state, "task_id": task["id"]}
