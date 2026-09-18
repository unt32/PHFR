"""Backward-compatible entrypoint.

The implementation now lives in the ``app`` package (see ``app/main.py``).
This module exists purely so that ``uvicorn api:app`` - and any other
tooling that still points at the old ``api.py`` - keeps working unchanged.
Prefer running ``uvicorn app.main:app`` directly in new setups.
"""

from app.main import app

__all__ = ["app"]
