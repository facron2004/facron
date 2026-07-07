"""Shared pytest fixtures and configuration.

Forces a temp-file SQLite DB so the API (running in a worker thread via
TestClient) shares the same database as the test process. In-memory SQLite
uses SingletonThreadPool which gives each thread its own DB, breaking schema
setup for the API.
"""

from __future__ import annotations

import os
import tempfile

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP_DB.name}")
