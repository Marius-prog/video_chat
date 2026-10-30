"""ASGI application: JSON API + security middleware + the Flet web client, on one origin."""
from __future__ import annotations

import logging
from typing import Optional

import flet.fastapi as flet_fastapi
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import ASSETS_DIR, Settings
from ..container import Container, build_container
from ..services.errors import ServiceError
from . import api
from .middleware import HostGuardMiddleware, SecurityHeadersMiddleware

log = logging.getLogger("videochat.web")


def _install_api(app, container: Container) -> None:
    app.state.container = container
    app.add_exception_handler(ServiceError, api.service_error_handler)
    app.add_exception_handler(StarletteHTTPException, api.http_error_handler)
    app.add_exception_handler(RequestValidationError, api.validation_error_handler)
    app.add_exception_handler(Exception, api.unhandled_error_handler)
    app.include_router(api.router)


def _install_middleware(app, settings: Settings) -> None:
    # Starlette runs the *last* added middleware first: the host guard sees the request before anything else.
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(HostGuardMiddleware, settings=settings)


def create_api_app(container: Container):
    """The API and middleware without the Flet client (used by tests and for API-only deployments)."""
    app = flet_fastapi.FastAPI()
    _install_api(app, container)
    _install_middleware(app, container.settings)
    return app


def create_app(settings: Optional[Settings] = None, *, container: Optional[Container] = None):
    from ..ui.app import make_main  # imported lazily so the API can be used without the UI stack

    settings = settings or (container.settings if container else Settings.from_env())
    container = container or build_container(settings)
    container.housekeeping()

    app = flet_fastapi.FastAPI()
    _install_api(app, container)  # must be registered before the Flet mount at "/", or Flet would shadow it
    app.mount(
        "/",
        flet_fastapi.app(
            make_main(container),
            assets_dir=str(ASSETS_DIR),
            no_cdn=True,  # serve Flutter/CanvasKit from this server: no third-party hosts at runtime
            app_name="Secure Video Chat",
            app_short_name="VideoChat",
            app_description="Video rooms with signed entry tickets",
        ),
    )
    _install_middleware(app, settings)
    if not settings.rtc_configured:
        log.warning("AGORA_APP_ID / AGORA_APP_CERTIFICATE not set: the UI works but rooms cannot start video.")
    return app
