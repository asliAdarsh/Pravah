"""Serve a built ``dist`` the way Vercel and Netlify do, for local checking.

A plain ``python -m http.server`` does not do SPA fallback, so deep links like
``/validation`` come back 404 and the check tells you nothing about the deploy.
This mirrors the two rules the real hosts apply:

* an existing file under the document root is served as-is — so the exported
  API snapshot under ``/api`` is never swallowed by the app shell; and
* anything else falls back to ``index.html`` so a deep link renders the app.

It is a verification aid, not a production server.

    python frontend/tools/serve_dist.py --dir frontend/dist --port 4173
"""

from __future__ import annotations

import argparse
import mimetypes
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class SpaHandler(SimpleHTTPRequestHandler):
    """Serve a real file when one exists, else the app shell."""

    def translate_path(self, path: str) -> str:
        resolved = Path(super().translate_path(path))
        if resolved.is_dir():
            index = resolved / "index.html"
            if index.is_file():
                return str(index)
        if resolved.is_file():
            return str(resolved)
        # No such file: the SPA rewrite both hosts apply.
        return str(Path(self.directory) / "index.html")

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *_args) -> None:  # quiet
        return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="dist", help="the built directory to serve")
    parser.add_argument("--port", type=int, default=4173)
    args = parser.parse_args()

    root = Path(args.dir).resolve()
    if not (root / "index.html").is_file():
        parser.error(f"{root}/index.html not found — build first")
    mimetypes.add_type("application/json", ".json")

    handler = lambda *a, **kw: SpaHandler(*a, directory=str(root), **kw)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"serving {root} with SPA fallback on http://127.0.0.1:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
