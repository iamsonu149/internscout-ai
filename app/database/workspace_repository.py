"""Worker-only persistence. Every operation is scoped to one claimed task owner."""

from datetime import datetime, timedelta, timezone

from app.database.repository import TRUST_RANK
from app.models import utcnow
from app.services.credit_budget import BudgetExceeded
from app.services.deduplicator import fingerprint, normalize_url
from app.services.workspace_store import WorkspaceError


class WorkspaceRepository:
    def __init__(self, store, task):
        self.store, self.task, self.owner = store, task, task["user_id"]
        self.rows = []
        for offset in range(0, 100000, 500):
            rows = self.read("opportunities", {"offset": offset, "limit": 500, "order": "id"})
            self.rows.extend(rows)
            if len(rows) < 500:
                break
        else:
            raise WorkspaceError("Workspace requires an archive before further discovery.")

    def read(self, table, params=None):
        return self.store.call(
            "GET", "/rest/v1/" + table, params={**(params or {}), "user_id": f"eq.{self.owner}"}
        )

    def recently_seen(self, url, days):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        return bool(
            self.read(
                "discovery_sources",
                {
                    "url": f"eq.{normalize_url(url)}",
                    "checked_at": f"gt.{cutoff}",
                    "outcome": "neq.ERROR",
                    "limit": 1,
                },
            )
        )

    def record_source(self, url, outcome):
        self.store.call(
            "POST",
            "/rest/v1/discovery_sources",
            params={"on_conflict": "user_id,url"},
            headers={"Prefer": "resolution=merge-duplicates"},
            json={
                "user_id": self.owner,
                "url": normalize_url(url),
                "outcome": outcome,
                "checked_at": utcnow(),
            },
        )

    def save(self, job, match, rejected=False, reasons=None):
        fp = fingerprint(job)
        application = normalize_url(job.application_url) if job.application_url else None
        old = next(
            (
                r
                for r in self.rows
                if r["fingerprint"] == fp
                or r["payload"].get("source_url") == normalize_url(job.source_url)
                or (application and r["payload"].get("application_url") == application)
            ),
            None,
        )
        payload = job.to_dict()
        payload.update(application_url=application, match=match.to_dict())
        if rejected:
            payload.update(rejection_reasons=reasons or [], decision="EXCLUDED")
        if old:
            payload["discovered_at"] = old["payload"].get("discovered_at", old["created_at"])
            if (
                not rejected
                and old["payload"].get("application_url") != application
                and TRUST_RANK.get(job.verification_status, 0)
                < TRUST_RANK.get(old["payload"].get("verification_status"), 0)
            ):
                payload = old["payload"]
            changed = {"payload": payload, "screened_out": rejected, "updated_at": utcnow()}
            self.store.call(
                "PATCH",
                "/rest/v1/opportunities",
                params={"user_id": f"eq.{self.owner}", "id": f"eq.{old['id']}"},
                json=changed,
            )
            old.update(changed)
            return old["id"], False
        rows = self.store.call(
            "POST",
            "/rest/v1/opportunities",
            headers={"Prefer": "return=representation"},
            json={"user_id": self.owner, "fingerprint": fp, "payload": payload, "screened_out": rejected},
        )
        self.rows.extend(rows)
        return rows[0]["id"], True

    def upsert(self, job, match):
        return self.save(job, match)

    def jobs(self):
        return [
            dict(
                row["payload"],
                id=row["id"],
                status=row["status"],
                notes=row["notes"],
                last_seen=row["updated_at"],
            )
            for row in self.rows
            if not row["screened_out"]
        ]

    def rejections(self):
        return [
            dict(row["payload"], id=row["id"], last_seen=row["updated_at"])
            for row in self.rows
            if row["screened_out"]
        ]

    def record_rejection(self, job, match, reasons):
        self.save(job, match, rejected=True, reasons=reasons)

    def resolve_rejection(self, job):
        pass  # Accepted and rejected results share a single record in PostgreSQL.

    def invalidate(self, source_url, application_url, reasons):
        for row in self.rows:
            if row["payload"].get("source_url") == source_url or (
                application_url and row["payload"].get("application_url") == application_url
            ):
                payload = dict(
                    row["payload"],
                    verification_status="REJECTED",
                    verification_reasons=reasons,
                    rejection_reasons=reasons,
                    decision="EXCLUDED",
                )
                changes = {"payload": payload, "screened_out": True, "updated_at": utcnow()}
                self.store.call(
                    "PATCH",
                    "/rest/v1/opportunities",
                    params={"user_id": f"eq.{self.owner}", "id": f"eq.{row['id']}"},
                    json=changes,
                )
                row.update(changes)

    def start_run(self):
        return self.task["id"]

    def finish_run(self, run_id, metrics, state):
        pass  # The worker finishes the claimed queue entry after the pipeline returns.


class WorkspaceBudget:
    def __init__(self, store, task, limit):
        self.store, self.task, self.limit = store, task, limit

    def cutoff(self):
        return (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    def reserve(self, endpoint, cost):
        try:
            return self.store.call(
                "POST",
                "/rest/v1/rpc/reserve_discovery_credit",
                json={
                    "p_task": self.task["id"],
                    "p_claim": self.task["claim_token"],
                    "p_endpoint": endpoint,
                    "p_cost": cost,
                },
            )
        except WorkspaceError:
            raise BudgetExceeded("Budget reservation denied; no paid request sent.") from None

    def finish(self, reservation, reported=None, request_id=None):
        self.store.call(
            "PATCH",
            "/rest/v1/credit_reservations",
            params={
                "user_id": f"eq.{self.task['user_id']}",
                "task_id": f"eq.{self.task['id']}",
                "id": f"eq.{reservation}",
            },
            json={
                "reported": reported if type(reported) is int and reported >= 0 else None,
                "request_id": str(request_id)[:100] if request_id else None,
            },
        )

    def summary(self):
        rows = []
        for offset in range(0, 100000, 500):
            batch = self.store.call(
                "GET",
                "/rest/v1/credit_reservations",
                params={
                    "user_id": f"eq.{self.task['user_id']}",
                    "created_at": f"gte.{self.cutoff()}",
                    "select": "reserved,reported",
                    "order": "created_at,id",
                    "offset": offset,
                    "limit": 500,
                },
            )
            rows.extend(batch)
            if len(batch) < 500:
                break
        used = sum(max(r["reserved"], r["reported"] or 0) for r in rows)
        return {
            "credit_limit": self.limit,
            "credits_budgeted_7d": used,
            "credits_provider_reported_7d": sum(r["reported"] or 0 for r in rows),
            "credit_requests_without_reported_cost": sum(r["reported"] is None for r in rows),
            "credit_remaining_budget": max(0, self.limit - used),
        }
