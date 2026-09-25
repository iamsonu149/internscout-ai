"""Copy public styling for Vercel's CDN; never include private app data."""

from pathlib import Path
from shutil import copyfile

target = Path("public/static")
target.mkdir(parents=True, exist_ok=True)
copyfile("app/static/style.css", target / "style.css")
