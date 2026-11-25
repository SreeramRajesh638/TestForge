"""ASGI entrypoint for hosting platforms (e.g. Vercel's FastAPI preset: `main:app`).

Locally, use `testforge serve` instead (port selection, browser, etc.)."""

from testforge.web.app import app

__all__ = ["app"]
