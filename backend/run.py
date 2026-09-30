"""Uvicorn launcher for the Pravah backend.

Run from the repository root with::

    python backend/run.py

Host/port can be overridden with ``PRAVAH_HOST`` / ``PRAVAH_PORT``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def main() -> None:
    """Start the uvicorn server."""
    import uvicorn

    host = os.environ.get("PRAVAH_HOST", "127.0.0.1")
    port = int(os.environ.get("PRAVAH_PORT", "8000"))
    reload = os.environ.get("PRAVAH_RELOAD", "0") not in ("0", "", "false", "False")
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level=os.environ.get("PRAVAH_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
