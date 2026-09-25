"""Vercel dashboard entrypoint. Discovery remains in GitHub Actions."""

from app.config import Settings
from app.dashboard import create_app

settings = Settings.from_env()
settings.dashboard_mode = "hosted"
app = create_app(settings)
