import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models import utcnow
from app.services.deduplicator import fingerprint, normalize_url

STATUSES = {"NEW", "SAVED", "APPLIED", "INTERVIEW", "REJECTED", "CLOSED"}
TRUST_RANK = {"REJECTED": 0, "UNVERIFIED": 1, "LIKELY_GENUINE": 2, "VERIFIED_ATS": 3, "VERIFIED_OFFICIAL": 4}


class Repository:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
                    application_url TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'NEW', notes TEXT NOT NULL DEFAULT '',
                    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS job_matches (
                    job_id INTEGER PRIMARY KEY REFERENCES jobs(id), payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources (
                    url TEXT PRIMARY KEY, checked_at TEXT NOT NULL, outcome TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS job_urls (
                    url TEXT PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id));
                CREATE TABLE IF NOT EXISTS rejected_matches (
                    id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
                    source_url TEXT NOT NULL, payload TEXT NOT NULL,
                    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS search_runs (
                    id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
                    state TEXT NOT NULL, metrics TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def recently_seen(self, url, days):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM sources WHERE url=? AND checked_at>? AND outcome!='ERROR'",
                    (normalize_url(url), cutoff),
                ).fetchone()
                is not None
            )

    def record_source(self, url, outcome):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO sources VALUES (?,?,?)", (normalize_url(url), utcnow(), outcome)
            )

    def upsert(self, job, match):
        url, fp, now = normalize_url(job.application_url), fingerprint(job), utcnow()
        job.application_url = url
        with self.connect() as db:
            old = db.execute(
                "SELECT * FROM jobs WHERE application_url=? OR fingerprint=? OR id IN (SELECT job_id FROM job_urls WHERE url=?) ORDER BY id LIMIT 1",
                (url, fp, url),
            ).fetchone()
            if old:
                previous = json.loads(old["payload"])
                # Keep the stronger canonical source, but accept refreshed evidence from the same posting.
                if (
                    old["application_url"] == url
                    or TRUST_RANK[job.verification_status] >= TRUST_RANK[previous["verification_status"]]
                ):
                    job.discovered_at = previous["discovered_at"]
                    db.execute(
                        "UPDATE jobs SET application_url=?,payload=?, last_seen=? WHERE id=?",
                        (url, json.dumps(job.to_dict()), now, old["id"]),
                    )
                    db.execute(
                        "INSERT OR REPLACE INTO job_matches VALUES (?,?)",
                        (old["id"], json.dumps(match.to_dict())),
                    )
                else:
                    db.execute("UPDATE jobs SET last_seen=? WHERE id=?", (now, old["id"]))
                db.execute("INSERT OR IGNORE INTO job_urls VALUES (?,?)", (old["application_url"], old["id"]))
                db.execute("INSERT OR IGNORE INTO job_urls VALUES (?,?)", (url, old["id"]))
                return old["id"], False
            cursor = db.execute(
                "INSERT INTO jobs(fingerprint,application_url,payload,first_seen,last_seen) VALUES (?,?,?,?,?)",
                (fp, url, json.dumps(job.to_dict()), now, now),
            )
            db.execute(
                "INSERT INTO job_matches VALUES (?,?)", (cursor.lastrowid, json.dumps(match.to_dict()))
            )
            db.execute("INSERT INTO job_urls VALUES (?,?)", (url, cursor.lastrowid))
            return cursor.lastrowid, True

    def jobs(self):
        with self.connect() as db:
            rows = db.execute(
                "SELECT j.*,m.payload AS match_json FROM jobs j JOIN job_matches m ON m.job_id=j.id ORDER BY j.first_seen DESC"
            ).fetchall()
        return [
            dict(
                json.loads(r["payload"]),
                id=r["id"],
                status=r["status"],
                notes=r["notes"],
                last_seen=r["last_seen"],
                match=json.loads(r["match_json"]),
            )
            for r in rows
        ]

    def update_tracking(self, job_id, status, notes):
        if status not in STATUSES or len(notes) > 5000:
            raise ValueError("Invalid tracking data")
        with self.connect() as db:
            return db.execute(
                "UPDATE jobs SET status=?, notes=? WHERE id=?", (status, notes, job_id)
            ).rowcount

    def record_rejection(self, job, match, reasons):
        source = normalize_url(job.source_url)
        payload = job.to_dict()
        try:
            payload["application_url"] = normalize_url(job.application_url)
        except ValueError:
            payload["application_url"] = None
        payload.update(
            match=match.to_dict(), rejection_reasons=list(dict.fromkeys(reasons)), decision="EXCLUDED"
        )
        fp, now = fingerprint(job), utcnow()
        with self.connect() as db:
            old = db.execute(
                "SELECT * FROM rejected_matches WHERE fingerprint=? OR source_url=? ORDER BY id LIMIT 1",
                (fp, source),
            ).fetchone()
            if old:
                payload["discovered_at"] = old["first_seen"]
                db.execute(
                    "UPDATE rejected_matches SET payload=?,last_seen=? WHERE id=?",
                    (json.dumps(payload), now, old["id"]),
                )
            else:
                db.execute(
                    "INSERT INTO rejected_matches(fingerprint,source_url,payload,first_seen,last_seen) VALUES (?,?,?,?,?)",
                    (fp, source, json.dumps(payload), payload["discovered_at"], now),
                )

    def resolve_rejection(self, job):
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM rejected_matches WHERE fingerprint=? OR source_url=?",
                (fingerprint(job), normalize_url(job.source_url)),
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload"])
                payload["decision"] = "NOW MATCHED"
                db.execute(
                    "UPDATE rejected_matches SET payload=?,last_seen=? WHERE id=?",
                    (json.dumps(payload), utcnow(), row["id"]),
                )

    def rejections(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM rejected_matches ORDER BY first_seen DESC").fetchall()
        return [dict(json.loads(row["payload"]), id=row["id"], last_seen=row["last_seen"]) for row in rows]

    def invalidate(self, source_url, application_url, reasons):
        with self.connect() as db:
            for row in db.execute("SELECT id,payload FROM jobs").fetchall():
                payload = json.loads(row["payload"])
                if payload["source_url"] == source_url or (
                    application_url and payload["application_url"] == application_url
                ):
                    payload["verification_status"] = "REJECTED"
                    payload["verification_reasons"] = reasons
                    db.execute(
                        "UPDATE jobs SET payload=?,last_seen=? WHERE id=?",
                        (json.dumps(payload), utcnow(), row["id"]),
                    )

    def start_run(self):
        with self.connect() as db:
            return db.execute(
                "INSERT INTO search_runs(started_at,state,metrics) VALUES (?,'RUNNING','{}')", (utcnow(),)
            ).lastrowid

    def finish_run(self, run_id, metrics, state):
        with self.connect() as db:
            db.execute(
                "UPDATE search_runs SET finished_at=?,state=?,metrics=? WHERE id=?",
                (utcnow(), state, json.dumps(metrics), run_id),
            )

    def runs(self):
        with self.connect() as db:
            return [
                dict(r, metrics=json.loads(r["metrics"]))
                for r in db.execute("SELECT * FROM search_runs ORDER BY id DESC LIMIT 20")
            ]
