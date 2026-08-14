"""Web UI: archive status, the schedule, the job queue, and one-off runs.

Enqueues rather than executes. The worker is the only thing that runs a job, so the timer
and the UI cannot start the same platform twice — see core.worker.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from social_archiver.api import content, control, frontend
from social_archiver.core.worker import running


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with running(control.queue):
        yield
    await content.reader.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Social Archiver", lifespan=lifespan)
    app.include_router(control.router)
    app.include_router(content.router)
    app.include_router(frontend.router)
    return app


app = create_app()
