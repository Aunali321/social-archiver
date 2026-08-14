"""Serves the built viewer (frontend/build) as a single-page app.

Real files are served as-is; any other non-API path gets index.html so client-side routes
survive a reload. Registered after the API routers, so /api always wins. When the build is
absent (a dev running `bun run dev` against this server), / says so instead of 404ing.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

BUILD_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "build"

router = APIRouter()


@router.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    if not BUILD_DIR.exists():
        return PlainTextResponse(
            "Frontend not built. Run `bun run build` in frontend/, or `bun run dev` for development.",
            status_code=503,
        )
    candidate = (BUILD_DIR / path).resolve() if path else BUILD_DIR / "index.html"
    if path and candidate.is_relative_to(BUILD_DIR) and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(BUILD_DIR / "index.html")
