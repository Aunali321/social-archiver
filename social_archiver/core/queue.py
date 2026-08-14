"""Durable job queue and schedule, shared by the scheduler and the web UI.

Jobs and schedules are rows, not in-memory state: a restart loses nothing, a run that dies is
visible rather than silently gone, and a paused platform stays paused.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import aiosqlite

from social_archiver.core import migrations
from social_archiver.core.sources import SourceRef, TrackedSource

MIGRATIONS = (
    (
        """
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
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_jobs_pending ON jobs(platform, status, id)",
        """
        CREATE TABLE IF NOT EXISTS schedules (
            platform TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL,
            categories TEXT NOT NULL,
            next_run TIMESTAMP NOT NULL
        )
        """,
    ),
    # A source is an account or a subreddit archived in its own right, rather than because it
    # turned up in something you interacted with. `target` says which one a job is for and is
    # null for the category jobs, which is what leaves their dedupe untouched.
    (
        "ALTER TABLE jobs ADD COLUMN target TEXT",
        """
        CREATE TABLE IF NOT EXISTS sources (
            platform TEXT NOT NULL,
            kind TEXT NOT NULL,
            target TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            added_at TIMESTAMP NOT NULL,
            last_run TIMESTAMP,
            PRIMARY KEY (platform, kind, target)
        )
        """,
    ),
    # Each platform cycles on its own clock. Nullable so a pre-existing row is recognisable
    # at seed time and backfilled with the configured default rather than a literal here.
    ("ALTER TABLE schedules ADD COLUMN interval_minutes INTEGER",),
)


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
    target: str | None = None  # which account or subreddit; only source jobs have one

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
            target=row["target"],
        )

    @property
    def flags(self) -> str:
        parts = [part for part in (self.target, self.category) if part]
        parts += [name for name, on in (("history", self.history), ("retry-failed", self.retry_failed)) if on]
        return " ".join(parts)


@dataclass(slots=True)
class Schedule:
    platform: str
    enabled: bool
    categories: list[str]
    next_run: datetime
    interval_minutes: int

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "Schedule":
        return cls(
            platform=row["platform"],
            enabled=bool(row["enabled"]),
            categories=json.loads(row["categories"]),
            next_run=datetime.fromisoformat(row["next_run"]),
            interval_minutes=row["interval_minutes"],
        )

    def due(self, now: datetime) -> bool:
        return self.enabled and self.next_run <= now


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
        await migrations.apply(self._connection, MIGRATIONS, "queue", self.db_path)
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
        target: str | None = None,
    ) -> Job | None:
        """Returns None when an identical job is already waiting: a scheduler tick that
        lands while the queue is backed up should not stack duplicates.

        Two source jobs for different targets are not duplicates, so the target takes part in
        the comparison; it is null for every category job, which leaves those unchanged."""
        duplicate = await self._connection.execute(
            """
            SELECT 1 FROM jobs
            WHERE platform = ? AND job = ? AND category IS ? AND target IS ? AND status IN (?, ?)
            """,
            (platform, job, category, target, JobStatus.QUEUED, JobStatus.RUNNING),
        )
        if await duplicate.fetchone():
            return None

        cursor = await self._connection.execute(
            """
            INSERT INTO jobs (platform, job, category, history, retry_failed, source, target, queued_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (platform, job, category, int(history), int(retry_failed), source, target, datetime.now().isoformat()),
        )
        await self._connection.commit()
        return await self.get(cursor.lastrowid)

    async def claim(self, platform: str) -> Job | None:
        """Take the oldest queued job for a platform and mark it running. One statement, so two
        workers can never claim the same row."""
        cursor = await self._connection.execute(
            """
            UPDATE jobs SET status = ?, started_at = ?
            WHERE id = (SELECT id FROM jobs WHERE platform = ? AND status = ? ORDER BY id LIMIT 1)
            RETURNING id
            """,
            (JobStatus.RUNNING, datetime.now().isoformat(), platform, JobStatus.QUEUED),
        )
        row = await cursor.fetchone()
        await self._connection.commit()
        return await self.get(row["id"]) if row else None

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

    async def ensure_schedule(self, platform: str, categories: list[str], next_run: datetime, interval_minutes: int):
        await self._connection.execute(
            """
            INSERT OR IGNORE INTO schedules (platform, enabled, categories, next_run, interval_minutes)
            VALUES (?, 1, ?, ?, ?)
            """,
            (platform, json.dumps(categories), next_run.isoformat(), interval_minutes),
        )
        # A row from before the interval column existed carries NULL; it takes the default
        # here, once, and is per-platform from then on.
        await self._connection.execute(
            "UPDATE schedules SET interval_minutes = ? WHERE platform = ? AND interval_minutes IS NULL",
            (interval_minutes, platform),
        )
        await self._connection.commit()

    async def schedules(self) -> list[Schedule]:
        cursor = await self._connection.execute("SELECT * FROM schedules ORDER BY platform")
        return [Schedule.from_row(row) for row in await cursor.fetchall()]

    async def schedule(self, platform: str) -> Schedule | None:
        cursor = await self._connection.execute("SELECT * FROM schedules WHERE platform = ?", (platform,))
        row = await cursor.fetchone()
        return Schedule.from_row(row) if row else None

    async def set_schedule(
        self, platform: str, enabled: bool, categories: list[str], next_run: datetime, interval_minutes: int
    ) -> bool:
        cursor = await self._connection.execute(
            "UPDATE schedules SET enabled = ?, categories = ?, next_run = ?, interval_minutes = ? WHERE platform = ?",
            (int(enabled), json.dumps(categories), next_run.isoformat(), interval_minutes, platform),
        )
        await self._connection.commit()
        return cursor.rowcount > 0

    # =========================================================================
    # Tracked sources
    # =========================================================================

    async def add_source(self, ref: SourceRef) -> bool:
        """False when it was already tracked, so adding twice is not an error."""
        cursor = await self._connection.execute(
            """
            INSERT OR IGNORE INTO sources (platform, kind, target, added_at)
            VALUES (?, ?, ?, ?)
            """,
            (ref.platform, ref.kind, ref.target, datetime.now().isoformat()),
        )
        await self._connection.commit()
        return cursor.rowcount > 0

    async def remove_source(self, ref: SourceRef) -> bool:
        cursor = await self._connection.execute(
            "DELETE FROM sources WHERE platform = ? AND kind = ? AND target = ?",
            (ref.platform, ref.kind, ref.target),
        )
        await self._connection.commit()
        return cursor.rowcount > 0

    async def set_source_enabled(self, ref: SourceRef, enabled: bool) -> bool:
        cursor = await self._connection.execute(
            "UPDATE sources SET enabled = ? WHERE platform = ? AND kind = ? AND target = ?",
            (int(enabled), ref.platform, ref.kind, ref.target),
        )
        await self._connection.commit()
        return cursor.rowcount > 0

    async def sources(self, platform: str | None = None) -> list[TrackedSource]:
        sql = "SELECT * FROM sources"
        params: tuple = ()
        if platform:
            sql += " WHERE platform = ?"
            params = (platform,)
        cursor = await self._connection.execute(sql + " ORDER BY platform, kind, target", params)
        return [TrackedSource.from_row(row) for row in await cursor.fetchall()]

    async def source(self, ref: SourceRef) -> TrackedSource | None:
        cursor = await self._connection.execute(
            "SELECT * FROM sources WHERE platform = ? AND kind = ? AND target = ?",
            (ref.platform, ref.kind, ref.target),
        )
        row = await cursor.fetchone()
        return TrackedSource.from_row(row) if row else None

    async def record_source_run(self, ref: "SourceRef"):
        """Only when it was walked. Where the next walk resumes from is the newest item the
        archive holds for the source, so it is not duplicated here to drift out of step."""
        await self._connection.execute(
            "UPDATE sources SET last_run = ? WHERE platform = ? AND kind = ? AND target = ?",
            (datetime.now().isoformat(), ref.platform, ref.kind, ref.target),
        )
        await self._connection.commit()
