"""`python -m scry_web` — a thin alternative to `scry web` for launching the UI."""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m scry_web",
                                 description="Launch the local scry web UI.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()
    from . import serve
    return serve(host=args.host, port=args.port, open_browser=not args.no_open,
                 config_path=args.config)


if __name__ == "__main__":
    sys.exit(main())
