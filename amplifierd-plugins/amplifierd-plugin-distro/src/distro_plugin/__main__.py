"""Dev harness for running the distro plugin as a standalone server."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_app() -> FastAPI:
    """Create and configure the FastAPI application with plugin routes."""
    from fastapi import FastAPI

    from distro_plugin import create_router

    app = FastAPI(title="Distro Plugin Dev")
    router = create_router(app.state)
    app.include_router(router)
    return app


def main() -> None:
    """Parse CLI args and start a uvicorn dev server."""
    parser = argparse.ArgumentParser(description="Distro plugin dev server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8411)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    import uvicorn

    if args.reload:
        uvicorn.run(
            "distro_plugin.__main__:create_app",
            host=args.host,
            port=args.port,
            reload=True,
            factory=True,
        )
    else:
        app = create_app()
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
