"""
Copy from pywebio.platform.fastapi
"""
import os
from contextlib import asynccontextmanager
from inspect import isawaitable

import uvicorn
from pywebio.platform.fastapi import (
    STATIC_PATH,
    Session,
    cdn_validation,
    get_free_port,
    open_webbrowser_on_server_started,
    start_remote_access_service,
    webio_routes,
)
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from module.webui.patch import patch_executor


class HeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache"
        return response


def build_lifespan(on_startup, on_shutdown, nested_lifespan=None):
    @asynccontextmanager
    async def lifespan(app):
        for handler in on_startup:
            result = handler()
            if isawaitable(result):
                await result

        try:
            if nested_lifespan is None:
                yield
            else:
                async with nested_lifespan(app) as state:
                    yield state
        finally:
            for handler in on_shutdown:
                result = handler()
                if isawaitable(result):
                    await result

    return lifespan


def asgi_app(
    applications,
    cdn=True,
    static_dir=None,
    debug=False,
    allowed_origins=None,
    check_origin=None,
    **starlette_settings
):
    debug = Session.debug = os.environ.get("PYWEBIO_DEBUG", debug)
    cdn = cdn_validation(cdn, "warn")
    if cdn is False:
        cdn = "pywebio_static"
    routes = webio_routes(
        applications,
        cdn=cdn,
        allowed_origins=allowed_origins,
        check_origin=check_origin,
    )
    if static_dir:
        routes.append(
            Mount("/static", app=StaticFiles(directory=static_dir), name="static")
        )
    routes.append(
        Mount(
            "/pywebio_static",
            app=StaticFiles(directory=STATIC_PATH),
            name="pywebio_static",
        )
    )
    middleware = [Middleware(HeaderMiddleware)]
    on_startup = list(starlette_settings.pop("on_startup", ()) or ())
    on_startup.insert(0, patch_executor)
    on_shutdown = list(starlette_settings.pop("on_shutdown", ()) or ())
    nested_lifespan = starlette_settings.pop("lifespan", None)
    return Starlette(
        routes=routes,
        middleware=middleware,
        debug=debug,
        lifespan=build_lifespan(on_startup, on_shutdown, nested_lifespan),
        **starlette_settings,
    )


def start_server(
    applications,
    port=0,
    host="",
    cdn=True,
    static_dir=None,
    remote_access=False,
    debug=False,
    allowed_origins=None,
    check_origin=None,
    auto_open_webbrowser=False,
    **uvicorn_settings
):
    if not host:
        host = "0.0.0.0"

    if port == 0:
        port = get_free_port()

    on_startup = []
    if auto_open_webbrowser:
        async def open_webbrowser():
            await open_webbrowser_on_server_started("localhost", port)

        on_startup.append(open_webbrowser)

    app = asgi_app(
        applications,
        cdn=cdn,
        static_dir=static_dir,
        debug=debug,
        allowed_origins=allowed_origins,
        check_origin=check_origin,
        on_startup=on_startup,
    )

    if remote_access:
        start_remote_access_service(local_port=port)

    uvicorn.run(app, host=host, port=port, **uvicorn_settings)
