"""Runs queued jobs, one platform at a time.

A worker per platform means Reddit and Twitter can archive together — different APIs, no
shared rate limit — while the same platform can never run twice at once, because a single
consumer drains its queue in order. The scheduler only enqueues, so a timer firing during a
long walk queues behind it rather than racing it.
"""

import asyncio
import importlib
import logging
import traceback
from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from social_archiver.core import config
from social_archiver.core.config import PLATFORMS
from social_archiver.core.queue import Job, JobQueue, Schedule

logger = logging.getLogger(__name__)

# Which flags each job accepts, mirroring core.cli.build_parser
JOB_FLAGS = {
    "archive": ("category", "history", "retry_failed"),
    "upload": ("retry_failed",),
    "caption": ("retry_failed",),
    "embed": ("retry_failed",),
    # A source job names what to walk; `history` means walk it all again rather than stopping
    # at the newest item already held.
    "source": ("target", "history"),
}

POLL_SECONDS = 2
SCHEDULE_POLL_SECONDS = 30


def source_kinds(platform: str) -> tuple[str, ...]:
    """Empty for a platform with no fetcher yet, which is what keeps the UI from offering one."""
    try:
        module = importlib.import_module(f"social_archiver.platforms.{platform}.sources")
    except ModuleNotFoundError:
        return ()
    fetcher = next(
        (getattr(module, name) for name in dir(module) if name.endswith("SourceFetcher")),
        None,
    )
    return tuple(getattr(fetcher, "kinds", ()))


def categories(platform: str) -> list[str]:
    archiver = importlib.import_module(f"social_archiver.platforms.{platform}.archiver")
    # Twitter models a category as an object pairing its name with a seed origin
    return [getattr(c, "name", c) for c in archiver.CATEGORIES]


def sidecar_module(platform: str):
    """The platform's sidecar: a long-lived companion run alongside its worker, exposing
    `run()`, `status()` and the pairing hooks — the WhatsApp bridge. None for a platform
    without one, which is every other platform."""
    try:
        return importlib.import_module(f"social_archiver.platforms.{platform}.sidecar")
    except ModuleNotFoundError:
        return None


def sidecar(platform: str):
    module = sidecar_module(platform)
    return module.run if module else None


def sidecar_status(platform: str) -> str | None:
    module = sidecar_module(platform)
    return module.status() if module else None


def next_run(interval_minutes: int) -> datetime:
    return datetime.now() + timedelta(minutes=interval_minutes)


async def enqueue_cycle(queue: JobQueue, schedule: Schedule, source: str) -> list[Job]:
    """Separate rows rather than one bundled run, so a category the API is refusing fails on its
    own instead of marking the whole cycle failed."""
    plan: list[tuple[str, str | None, str | None]] = [("archive", category, None) for category in schedule.categories]
    # Tracked sources sync on the same tick, before uploading, so a cycle's uploads carry
    # everything it archived rather than leaving source items until the next one.
    plan += [("source", None, tracked.target) for tracked in await queue.sources(schedule.platform) if tracked.enabled]
    if config.TELEGRAM_BOT_TOKEN:
        plan.append(("upload", None, None))
    # Caption before index: indexing reads captions, so it must follow.
    if config.CAPTIONING_ENABLED:
        plan.append(("caption", None, None))
    if config.EMBEDDING_ENABLED:
        plan.append(("embed", None, None))

    queued = []
    for job, category, target in plan:
        if enqueued := await queue.enqueue(schedule.platform, job, category=category, target=target, source=source):
            queued.append(enqueued)
    return queued


async def _invoke(job: Job):
    entry = importlib.import_module(f"social_archiver.platforms.{job.platform}.service")
    target = getattr(entry, job.job)
    flags = JOB_FLAGS[job.job]
    kwargs: dict[str, object] = {}
    if "history" in flags:
        kwargs["fetch_all"] = job.history
    if "category" in flags:
        kwargs["category"] = job.category
    if "retry_failed" in flags:
        kwargs["retry_failed"] = job.retry_failed
    if "target" in flags:
        if not job.target:
            raise ValueError(f"{job.platform} {job.job} job {job.id} has no target to walk")
        kwargs["target"] = job.target
        kwargs["full"] = kwargs.pop("fetch_all", False)
    await target(**kwargs)


async def worker(queue: JobQueue, platform: str):
    while True:
        job = await queue.claim(platform)
        if job is None:
            await asyncio.sleep(POLL_SECONDS)
            continue

        logger.info(f"Running {platform} {job.job} {job.flags}".rstrip())
        try:
            await _invoke(job)
        except Exception as e:
            logger.error(f"{platform} {job.job} failed: {e}", exc_info=True)
            await queue.finish(job.id, traceback.format_exc(limit=3))
        else:
            logger.info(f"Finished {platform} {job.job}")
            await queue.finish(job.id)


async def seed_schedules(queue: JobQueue):
    """Runs before anything can read a schedule, so nothing sees a platform that has no row yet."""
    for platform in PLATFORMS:
        await queue.ensure_schedule(
            platform, categories(platform), next_run(config.CHECK_INTERVAL_MINUTES), config.CHECK_INTERVAL_MINUTES
        )


async def scheduler(queue: JobQueue, platforms: Sequence[str] = PLATFORMS):
    """Queues a cycle for each platform this process serves, when its schedule falls due.

    The schedule is read every tick rather than held here, so a change takes effect without a
    restart. Nothing runs at startup: a restart is not a reason to hit a rate-limited API."""

    async def mine() -> list[Schedule]:
        return [s for s in await queue.schedules() if s.platform in platforms]

    if config.RUN_ON_START:
        for schedule in await mine():
            if schedule.enabled:
                await enqueue_cycle(queue, schedule, source="startup")
        logger.info("RUN_ON_START set, queued a cycle for every scheduled platform")

    logger.info(f"Scheduler idle for {', '.join(platforms)}; a due platform queues a cycle on its own interval")
    while True:
        await asyncio.sleep(SCHEDULE_POLL_SECONDS)
        try:
            now = datetime.now()
            for schedule in await mine():
                if not schedule.due(now):
                    continue
                # Reschedule first: a failure below costs one cycle, not an immediate retry loop.
                await queue.set_schedule(
                    schedule.platform,
                    True,
                    schedule.categories,
                    next_run(schedule.interval_minutes),
                    schedule.interval_minutes,
                )
                queued = await enqueue_cycle(queue, schedule, source="schedule")
                logger.info(f"Queued {len(queued)} job(s) for the {schedule.platform} cycle")
        except Exception:
            logger.exception("Scheduler tick failed; retrying next tick")


@asynccontextmanager
async def running(queue: JobQueue, platforms: Sequence[str] = PLATFORMS):
    """The queue, one worker per platform served, and the scheduler.

    A platform must be served by exactly one process: two workers on the same platform would
    archive it twice at once. Splitting platforms across processes is fine, overlapping is not."""
    await queue.connect()
    await seed_schedules(queue)
    tasks = [asyncio.create_task(worker(queue, platform)) for platform in platforms]
    tasks += [asyncio.create_task(run()) for platform in platforms if (run := sidecar(platform)) is not None]
    tasks.append(asyncio.create_task(scheduler(queue, platforms)))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await queue.close()
