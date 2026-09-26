"""Supabase Auth and PostgREST adapter; every data request uses the user's JWT."""

import httpx


class WorkspaceError(Exception):
    pass


class SessionExpired(WorkspaceError):
    pass


class WorkspaceStore:
    def __init__(self, url, public_key, client=None):
        self.url = url.rstrip("/")
        self.key = public_key
        self.client = client or httpx.Client(timeout=15, follow_redirects=False)

    def call(self, method, path, token=None, **kwargs):
        headers = {"apikey": self.key}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        headers.update(kwargs.pop("headers", {}))
        try:
            response = self.client.request(method, self.url + path, headers=headers, **kwargs)
        except httpx.HTTPError:
            raise WorkspaceError("Connection unavailable. Please try again shortly.") from None
        if response.status_code == 401:
            raise SessionExpired("Please sign in again.")
        if response.status_code >= 300:
            raise WorkspaceError("Request could not be completed. Check your details or try again later.")
        return response.json() if response.content else None

    def send_code(self, email):
        return self.call("POST", "/auth/v1/otp", json={"email": email, "create_user": False})

    def verify_code(self, email, code):
        return self.call("POST", "/auth/v1/verify", json={"email": email, "token": code, "type": "email"})

    def refresh(self, token):
        return self.call("POST", "/auth/v1/token?grant_type=refresh_token", json={"refresh_token": token})

    def user(self, token):
        return self.call("GET", "/auth/v1/user", token)

    def logout(self, token):
        self.call("POST", "/auth/v1/logout?scope=local", token)

    def profile(self, token, user_id):
        rows = self.call(
            "GET",
            "/rest/v1/profiles",
            token,
            params={"user_id": f"eq.{user_id}", "select": "document", "limit": 1},
        )
        return rows[0]["document"] if rows else {}

    def save_profile(self, token, user_id, document):
        self.call(
            "POST",
            "/rest/v1/profiles",
            token,
            params={"on_conflict": "user_id"},
            headers={"Prefer": "resolution=merge-duplicates"},
            json={"user_id": user_id, "document": document},
        )

    def opportunities(self, token, user_id, offset=0):
        return self.call(
            "GET",
            "/rest/v1/opportunities",
            token,
            params={
                "user_id": f"eq.{user_id}",
                "select": "*",
                "order": "created_at.desc",
                "limit": 50,
                "offset": offset,
            },
        )

    def track(self, token, user_id, job_id, status, notes):
        return self.call(
            "PATCH",
            "/rest/v1/opportunities",
            token,
            params={"user_id": f"eq.{user_id}", "id": f"eq.{job_id}"},
            headers={"Prefer": "return=representation"},
            json={"status": status, "notes": notes},
        )
