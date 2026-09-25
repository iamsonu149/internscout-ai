"""Durable rolling-seven-day reservations, including ambiguous paid-request failures."""

import json
from datetime import datetime, timedelta, timezone

from app.services.http import ProviderError


class BudgetExceeded(ProviderError):
    pass


class CreditBudget:
    def __init__(self, repo, limit, clock=None):
        self.repo, self.limit = repo, limit
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        with repo.connect() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='credit_ledger'").fetchone()
            db.execute("""CREATE TABLE IF NOT EXISTS credit_ledger (
                id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, endpoint TEXT NOT NULL,
                reserved INTEGER NOT NULL, reported INTEGER, request_id TEXT, outcome TEXT NOT NULL)""")
            if not exists:
                # Preserve conservative prior usage when upgrading an existing discovery database.
                for row in db.execute("SELECT started_at,metrics FROM search_runs").fetchall():
                    metrics = json.loads(row["metrics"])
                    estimate = metrics.get("queries", 0) * 4 + metrics.get("scraped", 0)
                    if estimate:
                        db.execute(
                            "INSERT INTO credit_ledger(created_at,endpoint,reserved,outcome) VALUES (?,?,?,?)",
                            (row["started_at"], "legacy", estimate, "historical estimate"),
                        )

    def cutoff(self):
        return (self.clock() - timedelta(days=7)).isoformat()

    def reserve(self, endpoint, cost):
        with self.repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute(
                "SELECT COALESCE(SUM(MAX(reserved,COALESCE(reported,0))),0) FROM credit_ledger WHERE created_at>=?",
                (self.cutoff(),),
            ).fetchone()[0]
            if used + cost > self.limit:
                raise BudgetExceeded("Weekly Firecrawl reservation limit reached")
            return db.execute(
                "INSERT INTO credit_ledger(created_at,endpoint,reserved,outcome) VALUES (?,?,?,?)",
                (self.clock().isoformat(), endpoint, cost, "pending/possibly charged"),
            ).lastrowid

    def finish(self, reservation, reported=None, request_id=None):
        reported = reported if type(reported) is int and reported >= 0 else None
        with self.repo.connect() as db:
            db.execute(
                "UPDATE credit_ledger SET reported=?,request_id=?,outcome='response received' WHERE id=?",
                (reported, str(request_id)[:100] if request_id else None, reservation),
            )

    def summary(self):
        with self.repo.connect() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(MAX(reserved,COALESCE(reported,0))),0), COALESCE(SUM(reported),0), SUM(reported IS NULL) FROM credit_ledger WHERE created_at>=?",
                (self.cutoff(),),
            ).fetchone()
        return {
            "credit_limit": self.limit,
            "credits_budgeted_7d": row[0],
            "credits_provider_reported_7d": row[1],
            "credit_requests_without_reported_cost": row[2] or 0,
            "credit_remaining_budget": max(0, self.limit - row[0]),
        }
