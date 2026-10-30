"""Entry point: ``python main.py`` serves the Flet web app on http://127.0.0.1:8550."""
from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from videochat.config import ConfigError, Settings
from videochat.web.app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Secure video chat (Flet + Agora)")
    parser.add_argument("--host", help="interface to bind (default: HOST env or 127.0.0.1)")
    parser.add_argument("--port", type=int, help="port to listen on (default: PORT env or 8550)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    app = create_app(settings)
    uvicorn.run(
        app,
        host=args.host or settings.host,
        port=args.port or settings.port,
        server_header=False,  # do not advertise the server software
        proxy_headers=bool(settings.trusted_proxies),
        forwarded_allow_ips=settings.trusted_proxies or None,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
