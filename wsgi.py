"""Vercel dashboard entrypoint. Discovery remains in GitHub Actions."""

import os

from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Settings
from app.dashboard import create_app

# Ensure hosted mode is visible to from_env() before it tries to mkdir
os.environ.setdefault("DASHBOARD_MODE", "hosted")
settings = Settings.from_env()
app = create_app(settings)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
