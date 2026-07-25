import asyncio
import logging
import time
from typing import Awaitable, Callable

import schedule

from social_archiver.core import config

logger = logging.getLogger(__name__)


class DaemonScheduler:
    """Runs a zero-arg async callable on a fixed interval forever. A failed cycle is logged
    and never kills the daemon.

    Nothing runs at startup unless RUN_ON_START is set: a container restart is not a reason
    to hit a rate-limited API, and restarts are exactly when you least want a long walk
    kicking off unattended."""

    def __init__(self, run_once: Callable[[], Awaitable[None]], interval_minutes: int):
        self.run_once = run_once
        self.interval_minutes = interval_minutes
        self.running = False

    def _run_cycle(self):
        try:
            asyncio.run(self.run_once())
        except Exception:
            logger.exception("Scheduled run failed; retrying next cycle")

    def run_daemon(self):
        schedule.every(self.interval_minutes).minutes.do(self._run_cycle)
        self.running = True

        if config.RUN_ON_START:
            logger.info("RUN_ON_START set, running one cycle now")
            self._run_cycle()

        next_run = schedule.next_run().strftime("%H:%M") if schedule.next_run() else "?"
        logger.info(f"Daemon idle; every {self.interval_minutes} minutes, first run at {next_run}")
        while self.running:
            schedule.run_pending()
            time.sleep(1)

    def stop(self):
        self.running = False
