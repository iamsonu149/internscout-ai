import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, abort, redirect, render_template, request, session, url_for

from app.database.repository import STATUSES, Repository
from app.services.dashboard_feed import SheetDashboardFeed
from app.services.verifier import EXPORTABLE


def create_app(settings, sheet_feed=None):
    hosted = settings.dashboard_mode == "hosted"
    if hosted and (
        not settings.sheet_id
        or len(settings.dashboard_password) < 24
        or len(settings.dashboard_secret_key) < 32
    ):
        raise ValueError("Hosted dashboard requires Sheets and strong authentication secrets")
    app = Flask(__name__)
    app.secret_key = settings.dashboard_secret_key if hosted else secrets.token_hex(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=hosted,
        MAX_CONTENT_LENGTH=16384,
        TRUSTED_HOSTS=[".vercel.app"] if hosted else ["127.0.0.1", "localhost"],
    )
    repo = None if hosted else Repository(settings.database_path)
    feed = sheet_feed or (SheetDashboardFeed(settings) if settings.sheet_id else None)

    @app.before_request
    def local_only():
        if hosted:
            auth = request.authorization
            if (
                not auth
                or auth.type.lower() != "basic"
                or not (
                    secrets.compare_digest(
                        (auth.username or "").encode(), settings.dashboard_username.encode()
                    )
                    and secrets.compare_digest(
                        (auth.password or "").encode(), settings.dashboard_password.encode()
                    )
                )
            ):
                return Response(
                    "Sign in to your private InternScout dashboard.",
                    401,
                    {"WWW-Authenticate": 'Basic realm="InternScout", charset="UTF-8"'},
                )
        elif request.remote_addr not in ("127.0.0.1", "::1", None):
            abort(403)
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)

    @app.after_request
    def secure(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        cloud = feed.read(force=request.args.get("refresh") == "1") if feed else None
        all_jobs = cloud["jobs"] if cloud else repo.jobs()
        rejected_jobs = cloud["rejected"] if cloud else repo.rejections()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=settings.recheck_days)).isoformat()
        for job in all_jobs + rejected_jobs:
            job.setdefault("status", "NEW")
            job.setdefault("notes", "")
            job["fresh"] = (job.get("last_seen") or "") >= cutoff
            # Main sheet does not contain a last-verification timestamp. Don't invent one.
            job["trusted"] = job["verification_status"] in EXPORTABLE and (bool(cloud) or job["fresh"])
            if job.get("deadline") and job["deadline"] != "Unknown":
                try:
                    job["trusted"] = (
                        job["trusted"] and date.fromisoformat(job["deadline"][:10]) >= date.today()
                    )
                except ValueError:
                    job["trusted"] = False
        counts = {s: sum(j["status"] == s for j in all_jobs) for s in STATUSES}
        counts["STRONG"] = sum(j["match"]["match_score"] >= 80 and j["trusted"] for j in all_jobs)
        counts["VERIFIED"] = sum(j["trusted"] for j in all_jobs)
        counts["SCREENED"] = len(rejected_jobs)
        view = request.args.get("view", "ALL")
        query = request.args.get("q", "").strip().lower()
        jobs = [
            j
            for j in (rejected_jobs if view == "SCREENED" else all_jobs)
            if (
                view == "ALL"
                or view == "SCREENED"
                or j["status"] == view
                or view == "STRONG"
                and j["match"]["match_score"] >= 80
                and j["trusted"]
                or view == "VERIFIED"
                and j["trusted"]
            )
            and (
                not query or query in (j["company"] + " " + j["title"] + " " + (j["location"] or "")).lower()
            )
        ]
        sort = request.args.get("sort", "match")
        jobs.sort(
            key=lambda j: j["match"]["match_score"] if sort == "match" else j["discovered_at"], reverse=True
        )
        runs = repo.runs() if repo else []
        return render_template(
            "dashboard.html",
            jobs=jobs,
            all_count=len(all_jobs),
            counts=counts,
            view=view,
            query=query,
            sort=sort,
            runs=runs,
            last=runs[0] if runs and not cloud else None,
            cloud=cloud,
            sheet_url=f"https://docs.google.com/spreadsheets/d/{settings.sheet_id}/edit"
            if settings.sheet_id
            else None,
            settings=settings,
            statuses=sorted(STATUSES),
            csrf=session["csrf"],
            profile_exists=Path(settings.profile_path).exists(),
        )

    @app.post("/jobs/<int:job_id>/tracking")
    def tracking(job_id):
        if not secrets.compare_digest(request.form.get("csrf", ""), session["csrf"]):
            abort(403)
        if feed:
            abort(409, description="Edit tracking in Google Sheets, then refresh the dashboard.")
        try:
            changed = repo.update_tracking(
                job_id, request.form.get("status", ""), request.form.get("notes", "")
            )
        except ValueError:
            abort(400)
        if not changed:
            abort(404)
        return redirect(url_for("index"), code=303)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app
