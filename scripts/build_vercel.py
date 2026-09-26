"""Copy public styling for Vercel's CDN; never include private app data."""

from pathlib import Path
from shutil import copyfile

target = Path("public/static")
target.mkdir(parents=True, exist_ok=True)
for name in ("style.css", "login.css", "login.js", "import.css", "workspace.css"):
    copyfile(Path("app/static") / name, target / name)
