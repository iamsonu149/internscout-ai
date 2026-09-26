"""Opt-in, isolated multi-user application; never reads the owner's local/Sheet data."""

import base64
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

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
    google_enabled = settings.workspace_google_enabled == "on"
    origin = settings.workspace_origin.rstrip("/")
    if google_enabled:
        site = urlsplit(origin)
        if (
            site.scheme != "https"
            or not site.hostname
            or site.path
            or site.query
            or site.fragment
            or site.username
            or site.port not in (None, 443)
        ):
            raise ValueError("Google login requires a fixed HTTPS WORKSPACE_ORIGIN.")
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

    def destination(value):
        return value if value in {"/", "/profile", "/connections", "/discovery"} else "/"

    def finish_login(payload, next_path="/"):
        auth = tokens(payload)
        if not store.user(auth["access_token"]).get("id"):
            raise ValueError("Invalid user")
        session.clear()
        session["csrf"] = secrets.token_urlsafe(32)
        g.set_auth = auth
        g.clear_auth = False
        return redirect(destination(next_path), code=303)

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
        if request.endpoint in {
            "static",
            "profile_template",
            "resume_prompt",
            "google_start",
            "google_callback",
        }:
            return None
        public_auth_page = request.endpoint in {"login", "signup"}
        encrypted = request.cookies.get(COOKIE, "")
        if public_auth_page and not encrypted:
            return None
        try:
            g.auth = json.loads(cipher.decrypt(encrypted.encode(), ttl=30 * 86400))
            if g.auth["expires_at"] <= time.time() + 60:
                g.auth = tokens(store.refresh(g.auth["refresh_token"]))
                g.set_auth = g.auth
            g.user = store.user(g.auth["access_token"])
            if not g.user.get("id"):
                raise SessionExpired()
        except (InvalidToken, ValueError, KeyError, SessionExpired):
            g.auth = None
            g.clear_auth = True
            if public_auth_page:
                return None
            return redirect(url_for("login", next=destination(request.path)), code=303)
        if public_auth_page:
            return redirect(destination(request.args.get("next")), code=303)

    @app.after_request
    def secure(response):
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Strict-Transport-Security": "max-age=31536000",
                "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
                + (
                    f" {settings.supabase_url.rstrip('/')} https://accounts.google.com"
                    if google_enabled
                    else ""
                ),
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
        error = None
        status = 200
        if request.method == "POST":
            try:
                email = request.form.get("email", "").strip().lower()
                password = request.form.get("password", "")
                if (
                    len(email) > 254
                    or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
                    or not 1 <= len(password) <= 1024
                ):
                    raise ValueError("Invalid credentials")
                # Supabase handles password verification and provider rate limits.
                # Never persist passwords in our database, session or logs.
                return finish_login(store.sign_in(email, password), request.form.get("next"))
            except (WorkspaceError, ValueError, KeyError):
                error = "Could not sign in. Check your email and password, or try again later."
                status = 401
        return render_template(
            "workspace/login.html",
            error=error,
            csrf=session["csrf"],
            google_enabled=google_enabled,
            next_path=destination(request.args.get("next")),
        ), status

    @app.get("/signup")
    def signup():
        return render_template("workspace/signup.html", csrf=session["csrf"], google_enabled=google_enabled)

    @app.post("/auth/google")
    def google_start():
        if not google_enabled:
            return render_template("workspace/signup.html", csrf=session["csrf"], google_enabled=False), 503
        if request.host_url.rstrip("/") != origin:
            return redirect(origin + "/login", code=303)
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(32)
        session["oauth"] = cipher.encrypt(
            json.dumps(
                {
                    "verifier": verifier,
                    "state": state,
                    "next": destination(request.form.get("next", "/profile")),
                }
            ).encode()
        ).decode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        callback = origin + "/auth/callback?" + urlencode({"flow": state})
        params = urlencode(
            {
                "provider": "google",
                "redirect_to": callback,
                "code_challenge": challenge,
                "code_challenge_method": "s256",
                "scopes": "openid email profile",
            }
        )
        response = redirect(settings.supabase_url.rstrip("/") + "/auth/v1/authorize?" + params, code=303)
        return response

    @app.get("/auth/callback")
    def google_callback():
        encrypted = session.pop("oauth", "")
        error_message = "Google sign-in could not finish. Please start again from this page."
        try:
            if not google_enabled or request.args.get("error"):
                if (
                    google_enabled
                    and request.args.get("error") == "server_error"
                    and request.args.get("error_code") == "unexpected_failure"
                    and request.args.get("error_description", "").startswith(
                        "Unable to exchange external code"
                    )
                ):
                    error_message = (
                        "Google sign-in is temporarily unavailable because the Google connection could not be verified. "
                        "The administrator needs to check the Google client ID and secret in Supabase. "
                        "You can still use your InternScout email and password below."
                    )
                raise ValueError("Google sign-in unavailable")
            flow = json.loads(cipher.decrypt(encrypted.encode(), ttl=600))
            supplied_state = request.args.get("flow", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", supplied_state) or not secrets.compare_digest(
                flow["state"], supplied_state
            ):
                raise ValueError("Invalid sign-in flow")
            code = request.args.get("code", "")
            if not 1 <= len(code) <= 2048:
                raise ValueError("Missing authorization code")
            return finish_login(store.exchange_code(code, flow["verifier"]), flow["next"])
        except (InvalidToken, WorkspaceError, ValueError, KeyError):
            return render_template(
                "workspace/login.html",
                csrf=session["csrf"],
                google_enabled=google_enabled,
                next_path="/",
                error=error_message,
            ), 400

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
