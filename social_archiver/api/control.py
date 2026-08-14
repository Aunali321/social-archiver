"""The /api/* control endpoints. Enqueue-only, like the rest of the package — see __init__."""

import asyncio
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel

from social_archiver.core import config
from social_archiver.core.queue import Job, JobQueue
from social_archiver.core.sources import SourceRef
from social_archiver.core.worker import (
    JOB_FLAGS,
    PLATFORMS,
    categories,
    enqueue_cycle,
    next_run,
    sidecar_module,
    sidecar_status,
    source_kinds,
)

queue = JobQueue(config.DATA_DIR / "jobs.db")

router = APIRouter()


@dataclass(slots=True)
class PlatformStatus:
    platform: str
    categories: list[str] = field(default_factory=list)
    total: int = 0
    archive: dict[str, int] = field(default_factory=dict)
    upload: dict[str, int] = field(default_factory=dict)
    embed: dict[str, int] = field(default_factory=dict)
    resumable: list[str] = field(default_factory=list)
    scheduled: bool = False
    scheduled_categories: list[str] = field(default_factory=list)
    next_run: str | None = None
    interval_minutes: int | None = None
    sidecar: str | None = None
    error: str | None = None


class RunRequest(BaseModel):
    platform: str
    job: str
    history: bool = False
    retry_failed: bool = False
    category: str | None = None
    target: str | None = None


class SourceRequest(BaseModel):
    platform: str
    target: str
    kind: str | None = None


class ScheduleRequest(BaseModel):
    platform: str
    enabled: bool
    categories: list[str]
    interval_minutes: int


def _source_counts() -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for platform in PLATFORMS:
        path = config.DATA_DIR / f"{platform}.db"
        if not path.exists():
            continue
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = db.execute(
                "SELECT source_target, count(*) FROM items WHERE platform = ? AND source_target IS NOT NULL GROUP BY 1",
                (platform,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []  # predates the column; it will appear once the archive is migrated
        finally:
            db.close()
        counts.update({(platform, target): n for target, n in rows})
    return counts


def _counts(db: sqlite3.Connection, column: str) -> dict[str, int]:
    return {row[0]: row[1] for row in db.execute(f"SELECT {column}, count(*) FROM items GROUP BY 1")}


def _status(platform: str) -> PlatformStatus:
    status = PlatformStatus(platform=platform, categories=categories(platform))
    status.sidecar = sidecar_status(platform)
    path = config.DATA_DIR / f"{platform}.db"
    if not path.exists():
        status.error = "nothing archived yet"
        return status

    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        status.total = db.execute("SELECT count(*) FROM items").fetchone()[0]
        status.archive = _counts(db, "archive_status")
        status.upload = _counts(db, "upload_status")
        status.embed = _counts(db, "embed_status")
        status.resumable = [row[0] for row in db.execute("SELECT category FROM fetch_cursors")]
    except sqlite3.Error as e:
        status.error = str(e)
    finally:
        db.close()
    return status


# Counting an archive means a pass over every row of it, and one of these is millions of rows.
# Two things follow. It cannot run on the event loop, where a multi-second scan stalls every
# worker sharing the process — an archive job stops making requests for as long as the scan
# lasts, which at a poll every few seconds is permanently. And it cannot run per request. So
# scans happen in a thread, at most one at a time, and the answer is held briefly.
#
# Only the archive totals are cached. The queue is read straight from its own small table on
# every request, so a job's status stays live while its item counts lag by up to a minute.
_SCAN_TTL = timedelta(seconds=60)
_scan_lock = asyncio.Lock()
_platform_scans: dict[str, tuple[datetime, PlatformStatus]] = {}
_source_scan: tuple[datetime, dict[tuple[str, str], int]] | None = None


def _fresh(stamped: tuple[datetime, object] | None) -> bool:
    return stamped is not None and datetime.now() - stamped[0] < _SCAN_TTL


async def _scan_platform(platform: str) -> PlatformStatus:
    """A copy, because the caller stamps schedule fields onto it that are not the archive's."""
    async with _scan_lock:
        if not _fresh(_platform_scans.get(platform)):
            _platform_scans[platform] = (datetime.now(), await asyncio.to_thread(_status, platform))
    return replace(_platform_scans[platform][1])


async def _scan_sources() -> dict[tuple[str, str], int]:
    global _source_scan
    async with _scan_lock:
        if not _fresh(_source_scan):
            _source_scan = (datetime.now(), await asyncio.to_thread(_source_counts))
    return _source_scan[1]


def _job_json(job: Job) -> dict:
    return {
        "id": job.id,
        "platform": job.platform,
        "job": job.job,
        "flags": job.flags,
        "status": str(job.status),
        "source": job.source,
        "queued_at": job.queued_at.strftime("%H:%M") if job.queued_at else None,
        "finished_at": job.finished_at.strftime("%H:%M") if job.finished_at else None,
        "error": job.error,
    }


@router.get("/api/status")
async def status() -> dict:
    schedules = {s.platform: s for s in await queue.schedules()}
    platforms = []
    for platform in PLATFORMS:
        entry = await _scan_platform(platform)
        if schedule := schedules.get(platform):
            entry.scheduled = schedule.enabled
            entry.scheduled_categories = schedule.categories
            entry.next_run = schedule.next_run.strftime("%H:%M") if schedule.enabled else None
            entry.interval_minutes = schedule.interval_minutes
        platforms.append(entry)

    return {
        "platforms": platforms,
        "active": [_job_json(j) for j in await queue.active()],
        "recent": [_job_json(j) for j in await queue.recent(12)],
    }


@router.post("/api/run")
async def run(request: RunRequest) -> dict[str, str]:
    if request.platform not in PLATFORMS or request.job not in JOB_FLAGS:
        return {"error": "unknown platform or job"}
    if request.category and request.category not in categories(request.platform):
        return {"error": f"{request.platform} has no category {request.category!r}"}
    if request.job == "source" and not request.target:
        return {"error": "a source job needs a target"}

    job = await queue.enqueue(
        request.platform,
        request.job,
        category=request.category,
        history=request.history,
        retry_failed=request.retry_failed,
        target=request.target,
    )
    if job is None:
        return {"error": f"{request.platform} {request.job} is already queued or running"}
    return {"started": f"queued {request.platform} {request.job} {job.flags}".rstrip()}


@router.post("/api/cycle/{platform}")
async def cycle(platform: str) -> dict[str, str]:
    """Queues exactly what the timer would."""
    schedule = await queue.schedule(platform)
    if schedule is None:
        return {"error": f"unknown platform {platform!r}"}

    queued = await enqueue_cycle(queue, schedule, source="web")
    if not queued:
        return {"error": f"nothing to queue for {platform}: no categories scheduled, or already queued"}
    return {"started": f"queued {len(queued)} job(s) for the {platform} cycle"}


@router.post("/api/pair/{platform}")
async def start_pairing(platform: str) -> dict[str, str]:
    module = sidecar_module(platform)
    if module is None:
        return {"error": f"{platform} has nothing to pair"}
    error = await module.pair_start()
    return {"error": error} if error else {"started": "pairing"}


@router.get("/api/pair/{platform}")
async def pairing_state(platform: str) -> dict:
    module = sidecar_module(platform)
    if module is None:
        return {"error": f"{platform} has nothing to pair"}
    return module.pair_state()


@router.post("/api/schedule")
async def set_schedule(request: ScheduleRequest) -> dict[str, str]:
    if request.platform not in PLATFORMS:
        return {"error": f"unknown platform {request.platform!r}"}
    if unknown := set(request.categories) - set(categories(request.platform)):
        return {"error": f"{request.platform} has no category {', '.join(sorted(unknown))}"}
    if request.interval_minutes < 1:
        return {"error": "the interval must be at least a minute"}

    # Resuming restarts the clock rather than firing immediately on a long-past due time.
    if not await queue.set_schedule(
        request.platform,
        request.enabled,
        request.categories,
        next_run(request.interval_minutes),
        request.interval_minutes,
    ):
        return {"error": f"no schedule for {request.platform}"}
    if not request.enabled:
        return {"started": f"{request.platform} schedule paused"}
    return {"started": f"{request.platform} scheduled: {', '.join(request.categories) or 'no categories'}"}


@router.get("/api/sources")
async def list_sources() -> dict:
    """Tracked sources with how much each has actually produced, so a source that is failing
    to fetch is distinguishable from one that simply has not run."""
    tracked = await queue.sources()
    counts = await _scan_sources()
    return {
        "kinds": {platform: list(source_kinds(platform)) for platform in PLATFORMS},
        "sources": [
            {
                "platform": s.platform,
                "kind": s.kind,
                "target": s.target,
                "enabled": s.enabled,
                "last_run": s.last_run.strftime("%Y-%m-%d %H:%M") if s.last_run else None,
                "items": counts.get((s.platform, s.target), 0),
            }
            for s in tracked
        ],
    }


def _resolve(request: SourceRequest) -> SourceRef | str:
    """The kind is optional in the request: a platform that offers exactly one does not need
    to be told which, and one that offers none cannot serve the request at all."""
    kinds = source_kinds(request.platform)
    if not kinds:
        return f"{request.platform} cannot archive sources yet"
    kind = request.kind or kinds[0]
    if kind not in kinds:
        return f"{request.platform} has no source kind {kind!r}; it offers {', '.join(kinds)}"
    target = request.target.strip().lstrip("@").removeprefix("r/")
    if not target:
        return "a source needs a target"
    return SourceRef(request.platform, kind, target)


@router.post("/api/sources/add")
async def add_source(request: SourceRequest) -> dict[str, str]:
    ref = _resolve(request)
    if isinstance(ref, str):
        return {"error": ref}
    if not await queue.add_source(ref):
        return {"error": f"{ref} is already tracked"}
    return {"started": f"tracking {request.platform} {ref}"}


@router.post("/api/sources/remove")
async def remove_source(request: SourceRequest) -> dict[str, str]:
    """Stops re-walking it. What it already archived stays, since deleting an archive is not
    something a checkbox should do."""
    ref = _resolve(request)
    if isinstance(ref, str):
        return {"error": ref}
    if not await queue.remove_source(ref):
        return {"error": f"{ref} was not tracked"}
    return {"started": f"stopped tracking {ref}; its items are kept"}


@router.post("/api/sources/enabled")
async def set_source_enabled(request: SourceRequest, enabled: bool) -> dict[str, str]:
    ref = _resolve(request)
    if isinstance(ref, str):
        return {"error": ref}
    if not await queue.set_source_enabled(ref, enabled):
        return {"error": f"{ref} is not tracked"}
    return {"started": f"{ref} {'enabled' if enabled else 'paused'}"}


@router.post("/api/sources/run")
async def run_source(request: SourceRequest, full: bool = False) -> dict[str, str]:
    """Runs a source whether or not it is tracked, so an account can be archived once without
    committing to re-walking it forever."""
    ref = _resolve(request)
    if isinstance(ref, str):
        return {"error": ref}
    job = await queue.enqueue(request.platform, "source", target=ref.target, history=full)
    if job is None:
        return {"error": f"{ref} is already queued or running"}
    return {"started": f"queued {'full walk' if full else 'sync'} of {ref}"}


@router.post("/api/cancel/{job_id}")
async def cancel(job_id: int) -> dict[str, str]:
    if await queue.cancel(job_id):
        return {"started": f"cancelled job {job_id}"}
    return {"error": "already running or finished"}
