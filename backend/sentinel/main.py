"""Run with:  cd backend && uvicorn sentinel.main:app --reload --port 8000"""

from sentinel.api.app import app

__all__ = ["app"]
