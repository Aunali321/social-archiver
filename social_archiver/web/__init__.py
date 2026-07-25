"""Web UI: archive status, the job queue, and one-off runs.

Enqueues rather than executes. The worker is the only thing that runs a job, so the timer
and the UI cannot start the same platform twice — see core.worker.
"""

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from social_archiver.core import config
from social_archiver.core.queue import Job, JobQueue
from social_archiver.core.worker import JOB_FLAGS, PLATFORMS, categories, scheduler, worker

queue = JobQueue(config.DATA_DIR / "jobs.db")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await queue.connect()
    tasks = [asyncio.create_task(worker(queue, platform)) for platform in PLATFORMS]
    tasks.append(asyncio.create_task(scheduler(queue)))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await queue.close()


app = FastAPI(title="Social Archiver", lifespan=lifespan)


@dataclass(slots=True)
class PlatformStatus:
    platform: str
    categories: list[str] = field(default_factory=list)
    total: int = 0
    archive: dict[str, int] = field(default_factory=dict)
    upload: dict[str, int] = field(default_factory=dict)
    embed: dict[str, int] = field(default_factory=dict)
    resumable: list[str] = field(default_factory=list)
    error: str | None = None


class RunRequest(BaseModel):
    platform: str
    job: str
    history: bool = False
    retry_failed: bool = False
    category: str | None = None


def _counts(db: sqlite3.Connection, column: str) -> dict[str, int]:
    return {row[0]: row[1] for row in db.execute(f"SELECT {column}, count(*) FROM items GROUP BY 1")}


def _status(platform: str) -> PlatformStatus:
    status = PlatformStatus(platform=platform, categories=categories(platform))
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


@app.get("/api/status")
async def status() -> dict:
    return {
        "platforms": [_status(platform) for platform in PLATFORMS],
        "active": [_job_json(j) for j in await queue.active()],
        "recent": [_job_json(j) for j in await queue.recent(12)],
    }


@app.post("/api/run")
async def run(request: RunRequest) -> dict[str, str]:
    if request.platform not in PLATFORMS or request.job not in JOB_FLAGS:
        return {"error": "unknown platform or job"}
    if request.category and request.category not in categories(request.platform):
        return {"error": f"{request.platform} has no category {request.category!r}"}

    job = await queue.enqueue(
        request.platform,
        request.job,
        category=request.category,
        history=request.history,
        retry_failed=request.retry_failed,
    )
    if job is None:
        return {"error": f"{request.platform} {request.job} is already queued or running"}
    return {"started": f"queued {request.platform} {request.job} {job.flags}".rstrip()}


@app.post("/api/cancel/{job_id}")
async def cancel(job_id: int) -> dict[str, str]:
    if await queue.cancel(job_id):
        return {"started": f"cancelled job {job_id}"}
    return {"error": "already running or finished"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX


INDEX = """<!doctype html>
<title>Social Archiver</title>
<style>
 :root { color-scheme: dark; --bg:#111418; --card:#191d23; --line:#262c34; --dim:#8b949e; --ok:#3fb950; --warn:#d29922; --bad:#f85149; --run:#58a6ff }
 body { margin:0; padding:2rem; background:var(--bg); color:#e6edf3; font:15px/1.55 ui-sans-serif,system-ui,sans-serif }
 h1 { font-size:1.05rem; font-weight:600; margin:0 0 1.25rem; letter-spacing:.02em }
 h2 { font-size:.8rem; font-weight:600; color:var(--dim); text-transform:uppercase; letter-spacing:.06em; margin:2rem 0 .6rem }
 .grid { display:grid; gap:1rem; grid-template-columns:repeat(auto-fit,minmax(380px,1fr)); max-width:1300px }
 .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:1.1rem 1.2rem }
 .name { font-weight:600; text-transform:capitalize; margin-bottom:.8rem }
 table.counts { width:100%; border-collapse:collapse; font-size:.85rem; margin-bottom:.5rem }
 table.counts td { padding:.15rem 0; color:var(--dim) }
 table.counts td+td { text-align:right; color:#e6edf3; font-variant-numeric:tabular-nums }
 .ok{color:var(--ok)} .warn{color:var(--warn)} .bad{color:var(--bad)} .run{color:var(--run)}
 .job { display:flex; align-items:center; gap:.45rem; padding:.4rem 0; border-top:1px solid var(--line); flex-wrap:wrap }
 .job b { font-weight:500; font-size:.82rem; width:4.2rem; color:#e6edf3 }
 button { background:#21262d; color:#e6edf3; border:1px solid var(--line); border-radius:6px; padding:.3rem .7rem; font-size:.79rem; cursor:pointer; font-family:inherit; margin-left:auto }
 button:hover { background:#30363d; border-color:#3d444d }
 select { background:#21262d; color:#e6edf3; border:1px solid var(--line); border-radius:6px; padding:.25rem .4rem; font-size:.78rem; font-family:inherit }
 label { font-size:.77rem; color:var(--dim); display:inline-flex; align-items:center; gap:.25rem; cursor:pointer; user-select:none }
 #msg { margin-bottom:1rem; min-height:1.2rem; font-size:.85rem; color:var(--dim) }
 .queue { max-width:1300px; font-size:.84rem; border-collapse:collapse; width:100% }
 .queue td { padding:.3rem .6rem .3rem 0; border-bottom:1px solid var(--line); color:var(--dim) }
 .queue td:first-child { color:#e6edf3; text-transform:capitalize }
 .queue .cancel { background:none; border:none; color:var(--dim); cursor:pointer; padding:0; margin:0; font-size:.78rem }
 .queue .cancel:hover { color:var(--bad); background:none }
</style>
<h1>Social Archiver</h1>
<div id="msg"></div>
<div class="grid" id="grid"></div>
<h2>Queue</h2><table class="queue" id="active"></table>
<h2>Recent</h2><table class="queue" id="recent"></table>
<script>
const FLAGS = {archive:['category','history','retry_failed'], upload:['retry_failed'], embed:['retry_failed'], run:['history']};
const cls = k => ({failed:'bad', archived:'ok', done:'ok', pending:'warn', running:'run', queued:'warn', interrupted:'bad'})[k] || '';
const counts = (label, o) => Object.entries(o || {}).map(([k, v]) =>
  `<tr><td>${label} ${k}</td><td class="${cls(k)}">${v.toLocaleString()}</td></tr>`).join('');

// The poll re-renders, which would otherwise discard a selection made mid-interaction.
const snapshot = () => Object.fromEntries([...document.querySelectorAll('#grid input,#grid select')]
  .map(el => [el.id, el.type === 'checkbox' ? el.checked : el.value]));
function restore(state) {
  for (const [id, value] of Object.entries(state)) {
    const el = document.getElementById(id);
    if (!el) continue;
    if (el.type === 'checkbox') el.checked = value; else el.value = value;
  }
}

function jobRow(p, job) {
  const f = FLAGS[job], id = `${p.platform}-${job}`;
  return `<div class="job"><b>${job}</b>
    ${f.includes('category') ? `<select id="c-${id}"><option value="">all categories</option>
      ${p.categories.map(c => `<option>${c}</option>`).join('')}</select>` : ''}
    ${f.includes('history') ? `<label><input type="checkbox" id="h-${id}"> history</label>` : ''}
    ${f.includes('retry_failed') ? `<label><input type="checkbox" id="r-${id}"> retry failed</label>` : ''}
    <button onclick="run('${p.platform}','${job}')">queue</button></div>`;
}

async function load() {
  const d = await (await fetch('/api/status')).json();
  const state = snapshot();
  document.getElementById('grid').innerHTML = d.platforms.map(p => `
    <div class="card">
      <div class="name">${p.platform}</div>
      ${p.error ? `<div style="color:var(--dim);font-size:.85rem;margin-bottom:.4rem">${p.error}</div>` : `
        <table class="counts">
          <tr><td>items</td><td>${p.total.toLocaleString()}</td></tr>
          ${counts('archive', p.archive)}${counts('upload', p.upload)}${counts('embed', p.embed)}
          ${p.resumable.length ? `<tr><td>resumable walk</td><td class="warn">${p.resumable.join(', ')}</td></tr>` : ''}
        </table>`}
      ${Object.keys(FLAGS).map(j => jobRow(p, j)).join('')}
    </div>`).join('');
  restore(state);

  document.getElementById('active').innerHTML = d.active.length ? d.active.map(j =>
    `<tr><td>${j.platform}</td><td>${j.job} ${j.flags}</td><td class="${cls(j.status)}">${j.status}</td>
     <td>${j.source}</td><td>${j.queued_at || ''}</td>
     <td>${j.status === 'queued' ? `<button class="cancel" onclick="cancel(${j.id})">cancel</button>` : ''}</td></tr>`
  ).join('') : '<tr><td style="color:var(--dim)">nothing queued</td></tr>';

  document.getElementById('recent').innerHTML = d.recent.map(j =>
    `<tr><td>${j.platform}</td><td>${j.job} ${j.flags}</td><td class="${cls(j.status)}">${j.status}</td>
     <td>${j.source}</td><td>${j.finished_at || ''}</td>
     <td style="color:var(--bad)">${(j.error || '').split('\\n').pop().slice(0, 60)}</td></tr>`
  ).join('') || '<tr><td style="color:var(--dim)">nothing yet</td></tr>';
}

async function post(url, body) {
  const res = await (await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: body ? JSON.stringify(body) : null})).json();
  document.getElementById('msg').textContent = res.error || res.started;
  load();
}

const run = (platform, job) => {
  const g = id => document.getElementById(id), key = `${platform}-${job}`;
  post('/api/run', {platform, job, category: g(`c-${key}`)?.value || null,
    history: g(`h-${key}`)?.checked || false, retry_failed: g(`r-${key}`)?.checked || false});
};
const cancel = id => post(`/api/cancel/${id}`);

load();
setInterval(load, 5000);
</script>
"""
