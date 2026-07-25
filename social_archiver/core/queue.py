"""Durable job queue shared by the scheduler and the web UI.

Jobs are rows, not in-memory state: a restart loses nothing, a run that dies is visible
rather than silently gone, and nothing can run the same platform twice at once because a
single worker drains each platform's queue in order.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import aiosqlite


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass(slots=True)
class Job:
    id: int
    platform: str
    job: str
    category: str | None
    history: bool
    retry_failed: bool
    status: JobStatus
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    source: str = "web"

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "Job":
        def when(name: str) -> datetime | None:
            return datetime.fromisoformat(row[name]) if row[name] else None

        return cls(
            id=row["id"],
            platform=row["platform"],
            job=row["job"],
            category=row["category"],
            history=bool(row["history"]),
            retry_failed=bool(row["retry_failed"]),
            status=JobStatus(row["status"]),
            queued_at=when("queued_at"),
            started_at=when("started_at"),
            finished_at=when("finished_at"),
            error=row["error"],
            source=row["source"],
        )

    @property
    def flags(self) -> str:
        parts = [self.category] if self.category else []
        parts += [name for name, on in (("history", self.history), ("retry-failed", self.retry_failed)) if on]
        return " ".join(parts)


class JobQueue:
    """One queue for every platform, in its own database so it is independent of the
    per-platform archives it drives."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connection: aiosqlite.Connection = None  # type: ignore

    async def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                job TEXT NOT NULL,
                category TEXT,
                history INTEGER NOT NULL DEFAULT 0,
                retry_failed INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued',
                source TEXT NOT NULL DEFAULT 'web',
                queued_at TIMESTAMP NOT NULL,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_pending ON jobs(platform, status, id);
        """)
        await self._connection.commit()
        await self._release_orphans()

    async def close(self):
        if self._connection:
            await self._connection.close()

    async def _release_orphans(self):
        """A job marked running at startup belonged to a process that died. Say so rather
        than leaving it running forever or pretending it finished."""
        await self._connection.execute(
            "UPDATE jobs SET status = ?, finished_at = ?, error = ? WHERE status = ?",
            (JobStatus.INTERRUPTED, datetime.now().isoformat(), "process exited mid-run", JobStatus.RUNNING),
        )
        await self._connection.commit()

    async def enqueue(
        self,
        platform: str,
        job: str,
        *,
        category: str | None = None,
        history: bool = False,
        retry_failed: bool = False,
        source: str = "web",
    ) -> Job | None:
        """Returns None when an identical job is already waiting: a scheduler tick that
        lands while the queue is backed up should not stack duplicates."""
        duplicate = await self._connection.execute(
            "SELECT 1 FROM jobs WHERE platform = ? AND job = ? AND status IN (?, ?)",
            (platform, job, JobStatus.QUEUED, JobStatus.RUNNING),
        )
        if await duplicate.fetchone():
            return None

        cursor = await self._connection.execute(
            """
            INSERT INTO jobs (platform, job, category, history, retry_failed, source, queued_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (platform, job, category, int(history), int(retry_failed), source, datetime.now().isoformat()),
        )
        await self._connection.commit()
        return await self.get(cursor.lastrowid)

    async def claim(self, platform: str) -> Job | None:
        """Take the oldest queued job for a platform and mark it running."""
        cursor = await self._connection.execute(
            "SELECT * FROM jobs WHERE platform = ? AND status = ? ORDER BY id LIMIT 1",
            (platform, JobStatus.QUEUED),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        await self._connection.execute(
            "UPDATE jobs SET status = ?, started_at = ? WHERE id = ?",
            (JobStatus.RUNNING, datetime.now().isoformat(), row["id"]),
        )
        await self._connection.commit()
        return await self.get(row["id"])

    async def finish(self, job_id: int, error: str | None = None):
        await self._connection.execute(
            "UPDATE jobs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
            (JobStatus.FAILED if error else JobStatus.DONE, datetime.now().isoformat(), error, job_id),
        )
        await self._connection.commit()

    async def cancel(self, job_id: int) -> bool:
        """Only a job that has not started can be cancelled; a running one owns an API walk."""
        cursor = await self._connection.execute(
            "UPDATE jobs SET status = ?, finished_at = ?, error = ? WHERE id = ? AND status = ?",
            (JobStatus.INTERRUPTED, datetime.now().isoformat(), "cancelled", job_id, JobStatus.QUEUED),
        )
        await self._connection.commit()
        return cursor.rowcount > 0

    async def get(self, job_id: int) -> Job | None:
        cursor = await self._connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        return Job.from_row(row) if row else None

    async def active(self) -> list[Job]:
        cursor = await self._connection.execute(
            "SELECT * FROM jobs WHERE status IN (?, ?) ORDER BY id", (JobStatus.RUNNING, JobStatus.QUEUED)
        )
        return [Job.from_row(row) for row in await cursor.fetchall()]

    async def recent(self, limit: int = 20) -> list[Job]:
        cursor = await self._connection.execute(
            "SELECT * FROM jobs WHERE status NOT IN (?, ?) ORDER BY id DESC LIMIT ?",
            (JobStatus.RUNNING, JobStatus.QUEUED, limit),
        )
        return [Job.from_row(row) for row in await cursor.fetchall()]


def to_json(job: Job) -> str:
    return json.dumps({"id": job.id, "platform": job.platform, "job": job.job, "status": str(job.status)})
