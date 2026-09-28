"""Web UI: archive status, the schedule, the job queue, and one-off runs. Also the MCP endpoint.

Enqueues rather than executes. The worker is the only thing that runs a job, so the timer
and the UI cannot start the same platform twice — see core.worker.

Everything but sign-in sits behind the owner's session (the UI) or an OAuth bearer token
(/mcp). The viewer's static files carry no data and stay public so /login can load.
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.authentication import AuthenticationMiddleware

from social_archiver import mcp_server
from social_archiver.api import auth, content, control, frontend
from social_archiver.core import config
from social_archiver.core.worker import running


def create_app() -> FastAPI:
    config.require(PUBLIC_URL=config.PUBLIC_URL, WEB_PASSWORD=config.WEB_PASSWORD)
    server = mcp_server.create(auth.provider, auth.mcp_auth())
    mcp_app = server.streamable_http_app(
        # Tools hold no per-session state, and stateless sessions survive a restart
        stateless_http=True,
        # DNS-rebinding checks guard unauthenticated local servers. Every request here needs a
        # bearer token a rebinding page cannot obtain, and an Origin allowlist would only turn
        # away MCP clients that run in a browser on another site.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await auth.store.connect()
        async with running(control.queue), server.session_manager.run():
            yield
        await content.reader.close()
        await mcp_server.reader.close()
        await auth.store.close()

    app = FastAPI(title="Social Archiver", lifespan=lifespan)
    # The SDK's own app wraps /mcp in these; its routes join this app, so they must too
    app.add_middleware(AuthContextMiddleware)
    app.add_middleware(AuthenticationMiddleware, backend=BearerAuthBackend(ProviderTokenVerifier(auth.provider)))
    app.include_router(auth.router)
    signed_in = [Depends(auth.require_session)]
    app.include_router(control.router, dependencies=signed_in)
    app.include_router(content.router, dependencies=signed_in)
    app.router.routes.extend(mcp_app.routes)
    app.include_router(frontend.router)
    return app
