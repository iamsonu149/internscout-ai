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
from app.services.profile_form import profile_from_form
from app.services.resume_profile import EMPTY_PROFILE, SCHEMA, parse_profile
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
    from app.resume_routes import register_resume_routes
    register_resume_routes(app, settings)

    def tokens(payload):
        return {
            "access_token": payload["access_token"],
            "refresh_token": payload["refresh_token"],
            "expires_at": time.time() + int(payload.get("expires_in", 3600)),
        }

    def destination(value):
        return value if value in {"/", "/profile", "/connections", "/discovery", "/shared"} else "/"

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
        g.auth = None
        # Logout must work even if refresh/user lookup is unavailable. Its route
        # checks CSRF itself and offers confirmation for stale forms.
        if request.endpoint == "logout":
            return None
        if request.method == "POST" and not secrets.compare_digest(
            request.form.get("csrf", ""), session["csrf"]
        ):
            return "Session expired or invalid form submission. Please go back, refresh the page, and try again.", 400
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
            g.profile = {}  # Initialize to prevent UndefinedError in error.html
            if not g.user.get("id"):
                raise SessionExpired()
            
            try:
                blocked = store.call("GET", "/rest/v1/blocked_users", g.auth["access_token"], params={"user_id": f"eq.{g.user['id']}"})
                if blocked:
                    if request.endpoint not in {"logout", "login"}:
                        g.clear_auth = True
                        return "Your account has been blocked.", 403
            except WorkspaceError:
                # If migration hasn't been run yet, assume not blocked
                pass

            g.is_admin = False
            if settings.admin_email and g.user.get("email", "").lower() == settings.admin_email.lower():
                g.is_admin = True
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

    @app.route("/logout", methods=["GET", "POST"])
    def logout():
        if request.method != "POST" or not secrets.compare_digest(
            request.form.get("csrf", ""), session["csrf"]
        ):
            return render_template("workspace/logout.html", csrf=session["csrf"])
        try:
            auth = json.loads(cipher.decrypt(request.cookies.get(COOKIE, "").encode(), ttl=30 * 86400))
            if isinstance(auth, dict) and isinstance(auth.get("access_token"), str):
                store.logout(auth["access_token"])
        except (InvalidToken, ValueError, KeyError, WorkspaceError):
            # Clear this browser's credentials even when the provider is down
            # or the encrypted cookie is no longer usable. Never refresh here.
            pass
        finally:
            session.clear()
            g.clear_auth = True
        return redirect(url_for("login"), code=303)

    @app.get("/")
    def index():
        from app.services.dashboard_feed import safe_url
        from app.services.workspace_dashboard import dashboard_view
        from app.services.workspace_profile import matching_profile

        token, owner = g.auth["access_token"], g.user["id"]
        profile = store.profile(token, owner) or {}
        g.profile = profile
        try:
            page = max(0, min(int(request.args.get("page", 0)), 10000))
        except ValueError:
            abort(400)
        query = request.args.get("q", "")[:200]
        sort = "recent" if request.args.get("sort") == "recent" else "match"
        data = dashboard_view(store.dashboard_rows(token, owner),
                              request.args.get("view", "ALL"), query, sort, page)
        jobs = store.opportunity_page(token, owner, data.pop("ids"))
        for item in jobs:
            item["safe_application_url"] = safe_url(
                item["payload"].get("application_url") or item["payload"].get("source_url")
            )
        connected = any(row.get("provider") == "firecrawl" for row in store.connections(token, owner))
        try:
            matching_profile(profile)
            profile_ready = True
        except (ValueError, TypeError, AttributeError):
            profile_ready = False
        searches = [row for row in store.tasks(token, owner) if row.get("kind") == "search"]
        if not profile_ready:
            empty_title, empty_text = "Complete your matching profile", "Review your skills, desired roles and eligibility preferences to enable matching."
            empty_url, empty_action = "/profile", "Review my profile"
        elif not connected:
            empty_title, empty_text = "Connect your search provider", "Connect Firecrawl to enable discovery for your workspace."
            empty_url, empty_action = "/connections", "Manage connections"
        else:
            empty_title, empty_text = "No opportunities in this view yet", "Your profile and connection are ready. New matches appear after a successful discovery run; you can also save a job link."
            empty_url, empty_action = "/discovery", "Save a job"
        return render_template(
            "workspace/home.html", **data, jobs=jobs, profile=profile, page=page,
            csrf=session["csrf"], statuses=sorted(STATUSES), query=query, sort=sort,
            screened=data["view"] == "SCREENED", connected=connected,
            profile_ready=profile_ready, worker_enabled=settings.workspace_worker_enabled == "on",
            latest=searches[0] if searches else None,
            empty_title=empty_title, empty_text=empty_text, empty_url=empty_url, empty_action=empty_action,
        )

    @app.route("/profile", methods=["GET", "POST"])
    def profile():
        error, preview = None, False
        status = 200
        if request.method == "POST":
            raw = request.form.get("document", "")
            try:
                existing = store.profile(g.auth["access_token"], g.user["id"]) or dict(EMPTY_PROFILE)
                if request.form.get("action") == "upload":
                    from app.services.resume_upload import (
                        MAX_TEXT,
                        extract_pdf,
                        parse_resume,
                        workspace_profile,
                    )
                    resume_text = request.form.get("resume_text", "").strip()
                    if resume_text:
                        # Text was extracted client-side (avoids Vercel body-size limit)
                        if len(resume_text) > MAX_TEXT:
                            raise ValueError("Resume text exceeds the maximum allowed length.")
                    else:
                        resume_text = extract_pdf(request.files.get("resume"))
                    extracted = parse_resume(resume_text, settings)
                    document = workspace_profile(extracted, existing)
                    raw = json.dumps(document)
                elif request.form.get("action") == "form_save":
                    document = profile_from_form(request.form, existing)
                    raw = json.dumps(document)
                elif request.form.get("action") == "manual":
                    document = dict(existing)

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
                if (
                    request.form.get("action") in {"save", "form_save"}
                    and request.form.get("confirmed") == "yes"
                ):
                    store.save_profile(g.auth["access_token"], g.user["id"], document)
                    return redirect(url_for("index"), code=303)
                preview = True
                raw = json.dumps(document, indent=2, ensure_ascii=False)
            except ValueError as exc:
                error, status = str(exc), 400
                document = locals().get("document", {})
            except Exception as exc:
                from app.services.resume_upload import ProvidersExhausted
                if isinstance(exc, ProvidersExhausted):
                    error, status = str(exc), 503
                    document = locals().get("document", {})
                else:
                    raise
        else:
            document = store.profile(g.auth["access_token"], g.user["id"]) or EMPTY_PROFILE
            raw = ""
        return render_template(
            "workspace/profile.html",
            raw=raw,
            document=document,
            preview=preview,
            schema=SCHEMA,
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
        from app.services.job_preview import validate_import_url

        error = None
        ready = settings.workspace_worker_enabled == "on"
        if request.method == "POST":
            action = request.form.get("action", "import")
            if action not in {"import", "accept"}:
                abort(400)
            try:
                if action == "accept":
                    if request.form.get("confirmed") != "yes":
                        raise ValueError("Review the job details and confirm before saving.")
                    store.accept_preview(
                        g.auth["access_token"],
                        request.form.get("preview_id", ""),
                        request.form.get("acknowledge") == "yes",
                    )
                else:
                    if not ready:
                        abort(503)
                    url = validate_import_url(request.form.get("job_url", ""))
                    store.enqueue(g.auth["access_token"], "import", url)
                return redirect(url_for("discovery"), code=303)
            except ValueError as exc:
                error = str(exc)
            except WorkspaceError:
                error = "Could not finish this request. Check your connection and profile, and wait for any current import to finish."
        return render_template(
            "workspace/discovery.html",
            tasks=store.tasks(g.auth["access_token"], g.user["id"]),
            previews=store.previews(g.auth["access_token"], g.user["id"]),
            error=error,
            ready=ready,
            csrf=session["csrf"],
        )

    @app.route("/shared", methods=["GET", "POST"])
    def shared():
        if request.method == "POST":
            store.save_shared(g.auth["access_token"], request.form.get("job_id", ""))
            return redirect(url_for("index"), code=303)
        try:
            page = max(0, min(int(request.args.get("page", 0)), 10000))
        except ValueError:
            abort(400)
        return render_template(
            "workspace/shared.html",
            jobs=store.shared_jobs(g.auth["access_token"], page * 50),
            page=page,
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
        action = request.form.get("action", "save")
        if action == "remove":
            store.remove_opportunity(g.auth["access_token"], g.user["id"], str(job_id))
            from flask import flash
            flash(f"Job removed. <form method='post' action='/jobs/{job_id}/undo' style='display:inline'><input type='hidden' name='csrf' value='{session.get('csrf')}'><button class='button secondary' style='padding:0.25rem 0.5rem; font-size:0.875rem'>Undo</button></form>", "success")
            return redirect(url_for("index"), code=303)
        status, notes = request.form.get("status", ""), request.form.get("notes", "")
        if status not in STATUSES or len(notes) > 5000:
            abort(400)
        if not store.track(g.auth["access_token"], g.user["id"], str(job_id), status, notes):
            abort(404)
        return redirect(url_for("index"), code=303)

    @app.post("/jobs/<uuid:job_id>/undo")
    def undo_remove(job_id):
        store.restore_opportunity(g.auth["access_token"], g.user["id"], str(job_id))
        return redirect(url_for("index"), code=303)


    @app.get("/admin")
    def admin_dashboard():
        if not getattr(g, "is_admin", False):
            abort(403)
        admin_store = WorkspaceStore(settings.supabase_url, settings.supabase_worker_key)
        g.profile = store.profile(g.auth["access_token"], g.user["id"]) or {}
        overview = admin_store.call("POST", "/rest/v1/rpc/admin_get_overview", json={}) or {}
        users = admin_store.call("POST", "/rest/v1/rpc/admin_list_users", json={}) or []
        feedback = admin_store.call("GET", "/rest/v1/feedback", params={"order": "created_at.desc", "limit": 50}) or []
        return render_template("workspace/admin.html", overview=overview, users=users, feedback=feedback, admin_email=settings.admin_email, csrf=session.get("csrf"))

    @app.post("/admin/users/<uuid:target_id>/block")
    def admin_block_user(target_id):
        if not getattr(g, "is_admin", False):
            abort(403)
        admin_store = WorkspaceStore(settings.supabase_url, settings.supabase_worker_key)
        action = request.form.get("action")
        if action == "block":
            admin_store.call("POST", "/rest/v1/blocked_users", json={"user_id": str(target_id), "reason": "Blocked by admin"})
        else:
            admin_store.call("DELETE", "/rest/v1/blocked_users", params={"user_id": f"eq.{target_id}"})
        return redirect("/admin", code=303)

    @app.post("/admin/feedback/<uuid:feedback_id>")
    def admin_update_feedback(feedback_id):
        if not getattr(g, "is_admin", False):
            abort(403)
        admin_store = WorkspaceStore(settings.supabase_url, settings.supabase_worker_key)
        status = request.form.get("status")
        if status in {"NEW", "IN_PROGRESS", "RESOLVED"}:
            admin_store.call("PATCH", "/rest/v1/feedback", params={"id": f"eq.{feedback_id}"}, json={"status": status})
        return redirect("/admin", code=303)

    @app.get("/feedback")
    def feedback_form():
        g.profile = store.profile(g.auth["access_token"], g.user["id"]) or {}
        return render_template("workspace/feedback.html", csrf=session.get("csrf"))

    @app.post("/feedback")
    def submit_feedback():
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        url = request.form.get("url", "").strip()
        if not subject or not message:
            return render_template("workspace/feedback.html", error="Subject and message are required.", csrf=session.get("csrf"))
        store.call("POST", "/rest/v1/feedback", g.auth["access_token"], json={"user_id": g.user["id"], "subject": subject, "message": message, "url": url})
        from flask import flash
        flash("Thank you for your feedback!", "success")
        return redirect(url_for("index"), code=303)

    return app
