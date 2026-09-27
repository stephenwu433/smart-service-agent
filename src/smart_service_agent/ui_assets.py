from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

WEB_DIR = Path(__file__).resolve().parent / "web"
ALLOWED_ASSETS = {"index.html", "styles.css", "app.js"}


def ui_file(name: str) -> FileResponse:
    if name not in ALLOWED_ASSETS:
        raise HTTPException(status_code=404, detail="ui asset not found")
    path = (WEB_DIR / name).resolve()
    if not path.is_relative_to(WEB_DIR.resolve()) or not path.is_file():
        raise HTTPException(status_code=404, detail="ui asset not found")
    return FileResponse(path)
