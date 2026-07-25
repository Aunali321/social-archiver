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

from social_archiver.core import config
from social_archiver.core.queue import Job, JobQueue

logger = logging.getLogger(__name__)

PLATFORMS = ("instagram", "reddit", "twitter")

# Which flags each job accepts, mirroring core.cli.build_parser
JOB_FLAGS = {
    "archive": ("category", "history", "retry_failed"),
    "upload": ("retry_failed",),
    "embed": ("retry_failed",),
    "run": ("history",),
}

POLL_SECONDS = 2


def categories(platform: str) -> list[str]:
    archiver = importlib.import_module(f"social_archiver.platforms.{platform}.archiver")
    # Twitter models a category as an object pairing its name with a seed origin
    return [getattr(c, "name", c) for c in archiver.CATEGORIES]


async def _invoke(job: Job):
    entry = importlib.import_module(f"social_archiver.platforms.{job.platform}.__main__")
    # `run` is run_all in the module; the CLI name is what the queue stores
    target = getattr(entry, "run_all" if job.job == "run" else job.job)
    flags = JOB_FLAGS[job.job]
    kwargs: dict[str, object] = {}
    if "history" in flags:
        kwargs["fetch_all"] = job.history
    if "category" in flags:
        kwargs["category"] = job.category
    if "retry_failed" in flags:
        kwargs["retry_failed"] = job.retry_failed
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


async def scheduler(queue: JobQueue):
    """Enqueues a full cycle per platform on the interval. Nothing runs at startup: a
    restart is not a reason to hit a rate-limited API, and RUN_ON_START overrides that."""
    interval = config.CHECK_INTERVAL_MINUTES * 60

    if config.RUN_ON_START:
        for platform in PLATFORMS:
            await queue.enqueue(platform, "run", source="startup")
        logger.info("RUN_ON_START set, queued a cycle for every platform")

    logger.info(f"Scheduler idle; queueing a cycle for every platform every {config.CHECK_INTERVAL_MINUTES} minutes")
    while True:
        await asyncio.sleep(interval)
        for platform in PLATFORMS:
            if await queue.enqueue(platform, "run", source="schedule"):
                logger.info(f"Queued scheduled run for {platform}")
            else:
                logger.info(f"Skipped scheduled run for {platform}: one is already queued or running")
