"""Opt-in, isolated multi-user application; never reads the owner's local/Sheet data."""

import json
import re
import secrets
import time
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, abort, g, redirect, render_template, request, session, url_for

from app.database.repository import STATUSES
from app.services.resume_profile import EMPTY_PROFILE, parse_profile
from app.services.workspace_store import SessionExpired, WorkspaceError, WorkspaceStore

COOKIE = "__Host-internscout-workspace"


def create_workspace_app(settings, store=None):
    parts = urlsplit(settings.supabase_url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or not parts.hostname.endswith(".supabase.co")
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or parts.username
        or parts.port not in (None, 443)
        or not settings.supabase_public_key
        or len(settings.dashboard_secret_key) < 32
    ):
        raise ValueError("Configure a Supabase project, public key and dashboard signing secret.")
    cipher = Fernet(settings.workspace_cookie_key.encode())
    store = store or WorkspaceStore(settings.supabase_url, settings.supabase_public_key)
    app = Flask(__name__)
    app.secret_key = settings.dashboard_secret_key
    app.config.update(
        SESSION_COOKIE_NAME="workspace_csrf",
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=100000,
        TRUSTED_HOSTS=[".vercel.app", "localhost", "127.0.0.1"],
    )

    def tokens(payload):
        return {
            "access_token": payload["access_token"],
            "refresh_token": payload["refresh_token"],
            "expires_at": time.time() + int(payload.get("expires_in", 3600)),
        }

    @app.before_request
    def authenticate():
        if request.routing_exception is not None:
            return None
        session.setdefault("csrf", secrets.token_urlsafe(32))
        if request.method == "POST" and not secrets.compare_digest(
            request.form.get("csrf", ""), session["csrf"]
        ):
            abort(400)
        g.auth = None
        if request.endpoint in {"login", "static", "profile_template", "resume_prompt"}:
            return None
        encrypted = request.cookies.get(COOKIE, "")
        try:
            g.auth = json.loads(cipher.decrypt(encrypted.encode(), ttl=30 * 86400))
            if g.auth["expires_at"] <= time.time() + 60:
                g.auth = tokens(store.refresh(g.auth["refresh_token"]))
                g.set_auth = g.auth
            g.user = store.user(g.auth["access_token"])
            if not g.user.get("id"):
                raise SessionExpired()
        except (InvalidToken, ValueError, KeyError, SessionExpired):
            g.clear_auth = True
            return redirect(url_for("login"), code=303)

    @app.after_request
    def secure(response):
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Strict-Transport-Security": "max-age=31536000",
                "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
            }
        )
        if getattr(g, "set_auth", None):
            value = cipher.encrypt(json.dumps(g.set_auth).encode()).decode()
            response.set_cookie(
                COOKIE, value, max_age=30 * 86400, secure=True, httponly=True, samesite="Lax", path="/"
            )
        if getattr(g, "clear_auth", False):
            response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="Lax", path="/")
        return response

    @app.errorhandler(WorkspaceError)
    def provider_error(_error):
        return render_template("workspace/error.html"), 503

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error, message = None, None
        if request.method == "POST":
            try:
                if request.form.get("action") == "verify":
                    email = session.get("pending_email", "")
                    code = request.form.get("code", "").strip()
                    if not email or not re.fullmatch(r"\d{6,10}", code):
                        raise ValueError("Enter the code from your email.")
                    auth = tokens(store.verify_code(email, code))
                    store.user(auth["access_token"])
                    session.clear()
                    session["csrf"] = secrets.token_urlsafe(32)
                    g.set_auth = auth
                    return redirect(url_for("index"), code=303)
                email = request.form.get("email", "").strip().lower()
                if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                    raise ValueError("Enter a valid email address.")
                if time.time() - session.get("code_sent_at", 0) < 60:
                    raise ValueError("Please wait a minute before requesting another code.")
                session["pending_email"] = email
                session["code_sent_at"] = time.time()
                try:
                    store.send_code(email)
                except WorkspaceError:
                    pass  # Do not reveal whether an address belongs to an invited user.
                message = "If this email has been invited, a sign-in code will arrive shortly."
            except (WorkspaceError, ValueError):
                error = "Could not sign in. Check your email/code, or wait a minute before trying again."
        return render_template(
            "workspace/login.html",
            error=error,
            message=message,
            pending=bool(session.get("pending_email")),
            csrf=session["csrf"],
        )

    @app.post("/logout")
    def logout():
        try:
            store.logout(g.auth["access_token"])
        finally:
            session.clear()
            g.clear_auth = True
        return redirect(url_for("login"), code=303)

    @app.get("/")
    def index():
        profile = store.profile(g.auth["access_token"], g.user["id"])
        try:
            page = max(0, min(int(request.args.get("page", 0)), 10000))
        except ValueError:
            abort(400)
        screened = request.args.get("view") == "screened"
        jobs = store.opportunities(g.auth["access_token"], g.user["id"], page * 50, screened)
        from app.services.dashboard_feed import safe_url

        for item in jobs:
            item["safe_application_url"] = safe_url(
                item["payload"].get("application_url") or item["payload"].get("source_url")
            )
        return render_template(
            "workspace/home.html",
            jobs=jobs,
            profile=profile,
            page=page,
            email=g.user.get("email", ""),
            csrf=session["csrf"],
            statuses=sorted(STATUSES),
            screened=screened,
        )

    @app.route("/profile", methods=["GET", "POST"])
    def profile():
        error, preview = None, False
        status = 200
        if request.method == "POST":
            raw = request.form.get("document", "")
            try:
                if request.form.get("action") == "manual":
                    document = store.profile(g.auth["access_token"], g.user["id"]) or dict(EMPTY_PROFILE)

                    def terms(name):
                        return [
                            value.strip() for value in request.form.get(name, "").split(",") if value.strip()
                        ]

                    year = request.form.get("year", "").strip()
                    document.update(
                        skills=terms("skills"),
                        current_country=request.form.get("country") or None,
                        education=[
                            {
                                "institution": request.form.get("institution") or None,
                                "degree": request.form.get("degree") or None,
                                "graduation_year": int(year) if year else None,
                                "graduation_status": request.form.get("graduation") or None,
                            }
                        ],
                    )
                    preferences = dict(document.get("preferences_to_confirm", {}))
                    preferences.update(
                        desired_roles=terms("roles"),
                        preferred_locations=terms("locations"),
                        full_time_available={"yes": True, "no": False}.get(request.form.get("full_time")),
                        countries_with_work_authorization=terms("work_countries"),
                        accept_undisclosed_compensation=request.form.get("undisclosed") == "yes",
                    )
                    document["preferences_to_confirm"] = preferences
                    raw = json.dumps(document)
                document = parse_profile(raw)
                if request.form.get("action") == "save" and request.form.get("confirmed") == "yes":
                    store.save_profile(g.auth["access_token"], g.user["id"], document)
                    return redirect(url_for("index"), code=303)
                preview = True
                raw = json.dumps(document, indent=2, ensure_ascii=False)
            except ValueError as exc:
                error, status = str(exc), 400
                document = {}
        else:
            document = store.profile(g.auth["access_token"], g.user["id"]) or EMPTY_PROFILE
            raw = json.dumps(document, indent=2, ensure_ascii=False)
        return render_template(
            "workspace/profile.html",
            raw=raw,
            document=document,
            preview=preview,
            error=error,
            csrf=session["csrf"],
        ), status

    @app.get("/profile/template")
    def profile_template():
        return app.response_class(json.dumps(EMPTY_PROFILE, indent=2), mimetype="application/json")

    @app.route("/connections", methods=["GET", "POST"])
    def connections():
        import httpx

        from app.services.firecrawl_service import Firecrawl
        from app.services.http import Http, ProviderError
        from app.services.provider_vault import ProviderVault

        error, message = None, None
        if request.method == "POST":
            if not settings.provider_encryption_key:
                abort(503)
            if request.form.get("action") == "remove":
                store.delete_connection(g.auth["access_token"], g.user["id"])
                return redirect(url_for("connections"), code=303)
            key = request.form.get("api_key", "").strip()
            if not 12 <= len(key) <= 256 or any(c.isspace() for c in key):
                error = "Enter a valid Firecrawl API key."
            else:
                try:
                    with httpx.Client(timeout=10, follow_redirects=False) as client:
                        Firecrawl(key, http=Http(client)).account_credits()
                    encrypted = ProviderVault(settings.provider_encryption_key).seal(
                        g.user["id"], "firecrawl", key
                    )
                    store.save_connection(g.auth["access_token"], g.user["id"], encrypted, key[-4:])
                    message = "Firecrawl connected. The connection check did not run a paid search or scrape."
                except ProviderError:
                    error = "Could not verify this key. Check your Firecrawl account or try again later."
        connections = store.connections(g.auth["access_token"], g.user["id"])
        return render_template(
            "workspace/connections.html",
            connections=connections,
            error=error,
            message=message,
            ready=bool(settings.provider_encryption_key),
            csrf=session["csrf"],
        )

    @app.route("/discovery", methods=["GET", "POST"])
    def discovery():
        from app.services.link_import import validate_link
        from app.services.workspace_profile import matching_profile

        error = None
        ready = settings.workspace_worker_enabled == "on"
        if request.method == "POST":
            if not ready:
                abort(503)
            try:
                matching_profile(store.profile(g.auth["access_token"], g.user["id"]))
                if request.form.get("action") == "settings":
                    limit = int(request.form.get("weekly_limit", "100"))
                    if not 0 <= limit <= 10000:
                        raise ValueError("Weekly credit limit must be between 0 and 10,000.")
                    store.save_discovery_settings(
                        g.auth["access_token"], g.user["id"], limit, request.form.get("scheduled") == "yes"
                    )
                else:
                    kind = request.form.get("kind", "search")
                    if kind not in ("search", "import"):
                        abort(400)
                    url = (
                        validate_link(request.form.get("job_url", ""), settings.sources_path)
                        if kind == "import"
                        else None
                    )
                    store.enqueue(g.auth["access_token"], kind, url)
                return redirect(url_for("discovery"), code=303)
            except ValueError as exc:
                error = str(exc)
            except WorkspaceError:
                error = "Could not queue this request. Connect Firecrawl first, finish any active task, and allow 15 minutes between requests."
        tasks = store.tasks(g.auth["access_token"], g.user["id"])
        config = store.discovery_settings(g.auth["access_token"], g.user["id"])
        return render_template(
            "workspace/discovery.html",
            tasks=tasks,
            config=config,
            error=error,
            ready=ready,
            csrf=session["csrf"],
        )

    @app.get("/profile/prompt")
    def resume_prompt():
        from pathlib import Path

        return app.response_class(
            Path("app/resources/resume-prompt.txt").read_text(encoding="utf-8"), mimetype="text/plain"
        )

    @app.post("/jobs/<uuid:job_id>/tracking")
    def tracking(job_id):
        status, notes = request.form.get("status", ""), request.form.get("notes", "")
        if status not in STATUSES or len(notes) > 5000:
            abort(400)
        if not store.track(g.auth["access_token"], g.user["id"], str(job_id), status, notes):
            abort(404)
        return redirect(url_for("index"), code=303)

    return app
