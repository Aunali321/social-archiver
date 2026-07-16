import asyncio
import logging
import time
from typing import Awaitable, Callable

import schedule

logger = logging.getLogger(__name__)


class DaemonScheduler:
    """Runs a zero-arg async callable immediately, then on a fixed interval
    forever. A failed cycle is logged and never kills the daemon."""

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
        logger.info(f"Scheduled to run every {self.interval_minutes} minutes")

        self.running = True
        self._run_cycle()

        logger.info("Entering daemon mode")
        while self.running:
            schedule.run_pending()
            time.sleep(1)

    def stop(self):
        self.running = False
