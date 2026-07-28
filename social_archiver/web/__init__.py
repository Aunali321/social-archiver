"""Web UI: archive status, the schedule, the job queue, and one-off runs.

Enqueues rather than executes. The worker is the only thing that runs a job, so the timer
and the UI cannot start the same platform twice — see core.worker.
"""

import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
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
    running,
    source_kinds,
)

queue = JobQueue(config.DATA_DIR / "jobs.db")


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with running(queue):
        yield


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
    scheduled: bool = False
    scheduled_categories: list[str] = field(default_factory=list)
    next_run: str | None = None
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
    schedules = {s.platform: s for s in await queue.schedules()}
    platforms = []
    for platform in PLATFORMS:
        entry = _status(platform)
        if schedule := schedules.get(platform):
            entry.scheduled = schedule.enabled
            entry.scheduled_categories = schedule.categories
            entry.next_run = schedule.next_run.strftime("%H:%M") if schedule.enabled else None
        platforms.append(entry)

    return {
        "interval_minutes": config.CHECK_INTERVAL_MINUTES,
        "platforms": platforms,
        "active": [_job_json(j) for j in await queue.active()],
        "recent": [_job_json(j) for j in await queue.recent(12)],
    }


@app.post("/api/run")
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


@app.post("/api/cycle/{platform}")
async def cycle(platform: str) -> dict[str, str]:
    """Queues exactly what the timer would."""
    schedule = await queue.schedule(platform)
    if schedule is None:
        return {"error": f"unknown platform {platform!r}"}

    queued = await enqueue_cycle(queue, schedule, source="web")
    if not queued:
        return {"error": f"nothing to queue for {platform}: no categories scheduled, or already queued"}
    return {"started": f"queued {len(queued)} job(s) for the {platform} cycle"}


@app.post("/api/schedule")
async def set_schedule(request: ScheduleRequest) -> dict[str, str]:
    if request.platform not in PLATFORMS:
        return {"error": f"unknown platform {request.platform!r}"}
    if unknown := set(request.categories) - set(categories(request.platform)):
        return {"error": f"{request.platform} has no category {', '.join(sorted(unknown))}"}

    # Resuming restarts the clock rather than firing immediately on a long-past due time.
    if not await queue.set_schedule(request.platform, request.enabled, request.categories, next_run()):
        return {"error": f"no schedule for {request.platform}"}
    if not request.enabled:
        return {"started": f"{request.platform} schedule paused"}
    return {"started": f"{request.platform} scheduled: {', '.join(request.categories) or 'no categories'}"}


@app.get("/api/sources")
async def list_sources() -> dict:
    """Tracked sources with how much each has actually produced, so a source that is failing
    to fetch is distinguishable from one that simply has not run."""
    tracked = await queue.sources()
    counts = _source_counts()
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


@app.post("/api/sources/add")
async def add_source(request: SourceRequest) -> dict[str, str]:
    ref = _resolve(request)
    if isinstance(ref, str):
        return {"error": ref}
    if not await queue.add_source(ref):
        return {"error": f"{ref} is already tracked"}
    return {"started": f"tracking {request.platform} {ref}"}


@app.post("/api/sources/remove")
async def remove_source(request: SourceRequest) -> dict[str, str]:
    """Stops re-walking it. What it already archived stays, since deleting an archive is not
    something a checkbox should do."""
    ref = _resolve(request)
    if isinstance(ref, str):
        return {"error": ref}
    if not await queue.remove_source(ref):
        return {"error": f"{ref} was not tracked"}
    return {"started": f"stopped tracking {ref}; its items are kept"}


@app.post("/api/sources/enabled")
async def set_source_enabled(request: SourceRequest, enabled: bool) -> dict[str, str]:
    ref = _resolve(request)
    if isinstance(ref, str):
        return {"error": ref}
    if not await queue.set_source_enabled(ref, enabled):
        return {"error": f"{ref} is not tracked"}
    return {"started": f"{ref} {'enabled' if enabled else 'paused'}"}


@app.post("/api/sources/run")
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


@app.post("/api/cancel/{job_id}")
async def cancel(job_id: int) -> dict[str, str]:
    if await queue.cancel(job_id):
        return {"started": f"cancelled job {job_id}"}
    return {"error": "already running or finished"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX


INDEX = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Social Archiver</title>
<style>
 :root {
   color-scheme: light dark;
   --bg:#f6f7f9; --card:#fff; --line:#d8dde3; --text:#1a1f26; --dim:#5c6773; --field:#fff;
   --ok:#1a7f37; --warn:#9a6700; --bad:#cf222e; --run:#0969da; --accent:#0969da;
   --s1:.35rem; --s2:.7rem; --s3:1.1rem; --s4:1.7rem; --radius:10px;
 }
 @media (prefers-color-scheme: dark) {
   :root {
     --bg:#111418; --card:#191d23; --line:#262c34; --text:#e6edf3; --dim:#8b949e; --field:#21262d;
     --ok:#3fb950; --warn:#d29922; --bad:#f85149; --run:#58a6ff; --accent:#58a6ff;
   }
 }
 * { box-sizing:border-box }
 body { margin:0; padding:var(--s4) var(--s3) 4rem; background:var(--bg); color:var(--text);
        font:15px/1.55 ui-sans-serif,system-ui,sans-serif }
 main { max-width:1320px; margin:0 auto }
 h1 { font-size:1.05rem; font-weight:600; margin:0; letter-spacing:.02em }
 h2 { font-size:.78rem; font-weight:600; color:var(--dim); text-transform:uppercase;
      letter-spacing:.07em; margin:var(--s4) 0 var(--s2) }
 h3 { font-size:.95rem; font-weight:600; margin:0; text-transform:capitalize }
 h4 { font-size:.7rem; font-weight:600; color:var(--dim); text-transform:uppercase;
      letter-spacing:.07em; margin:0 0 var(--s2) }
 .sub { margin:.2rem 0 0; font-size:.82rem; color:var(--dim) }
 #msg { min-height:1.3rem; margin:var(--s3) 0 0; font-size:.85rem; color:var(--accent) }
 #msg.error { color:var(--bad) }

 .grid { display:grid; gap:var(--s3); grid-template-columns:repeat(auto-fit,minmax(340px,1fr)) }
 .card { background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
         padding:var(--s3); display:flex; flex-direction:column; gap:var(--s3) }
 .card-head { display:flex; align-items:center; gap:var(--s2); justify-content:space-between }
 .block { border-top:1px solid var(--line); padding-top:var(--s3) }

 .pill { font-size:.72rem; font-weight:500; padding:.15rem .5rem; border-radius:99px;
         border:1px solid var(--line); color:var(--dim); white-space:nowrap }
 .pill.on { color:var(--ok); border-color:currentColor }
 .pill.off { color:var(--warn); border-color:currentColor }

 .stats { display:grid; grid-template-columns:1fr auto; gap:.1rem var(--s2); margin:0; font-size:.84rem }
 .stats dt { color:var(--dim) }
 .stats dd { margin:0; text-align:right; font-variant-numeric:tabular-nums }
 .ok{color:var(--ok)} .warn{color:var(--warn)} .bad{color:var(--bad)} .run{color:var(--run)}

 fieldset { border:0; margin:0 0 var(--s2); padding:0; display:flex; flex-wrap:wrap; gap:var(--s2) }
 legend { font-size:.78rem; color:var(--dim); padding:0; margin-bottom:var(--s1) }
 .row { display:flex; align-items:center; gap:var(--s2); flex-wrap:wrap }
 .row .hint { font-size:.78rem; color:var(--dim); margin-right:auto }
 .job { display:grid; grid-template-columns:3.6rem 1fr auto; align-items:center;
        gap:var(--s2); padding:var(--s1) 0 }
 .job b { font-weight:500; font-size:.82rem }
 .job .controls { display:flex; align-items:center; gap:var(--s2); flex-wrap:wrap }
 .job .hint { font-size:.78rem; color:var(--dim) }
 @media (max-width:560px) {
   .job { grid-template-columns:1fr auto }
   .job b { grid-column:1 / -1 }
 }

 button, select { font-family:inherit; font-size:.79rem; color:var(--text); background:var(--field);
                  border:1px solid var(--line); border-radius:6px; padding:.3rem .65rem; cursor:pointer }
 button:hover, select:hover { border-color:var(--accent) }
 button.primary { border-color:var(--accent); color:var(--accent) }
 button.link { background:none; border:none; color:var(--dim); padding:0 }
 button.link:hover { color:var(--bad) }
 :focus-visible { outline:2px solid var(--accent); outline-offset:2px }
 label { font-size:.79rem; color:var(--dim); display:inline-flex; align-items:center;
         gap:.3rem; cursor:pointer; user-select:none }
 input[type=checkbox] { accent-color:var(--accent); margin:0 }

 table { width:100%; border-collapse:collapse; font-size:.84rem }
 td, th { padding:.35rem var(--s2) .35rem 0; border-bottom:1px solid var(--line);
          color:var(--dim); text-align:left; font-weight:400 }
 th { font-size:.72rem; text-transform:uppercase; letter-spacing:.05em }
 td:first-child { color:var(--text); text-transform:capitalize }
 td.err { color:var(--bad); font-family:ui-monospace,monospace; font-size:.76rem }
 .empty, td.empty { color:var(--dim); font-style:italic; text-transform:none }
 .scroll { overflow-x:auto }
</style>
<main>
<header>
  <h1>Social Archiver</h1>
  <p class="sub" id="interval"></p>
</header>
<p id="msg" role="status" aria-live="polite"></p>

<h2>Platforms</h2>
<div class="grid" id="grid"></div>

<h2>Sources</h2>
<div class="card" id="sources"></div>

<h2>Queue</h2>
<div class="scroll"><table id="active"></table></div>

<h2>History</h2>
<div class="scroll"><table id="recent"></table></div>
</main>
<script>
const FLAGS = {archive:['category','history','retry_failed'], upload:['retry_failed'], embed:['retry_failed']};
// source jobs are driven from the Sources section, not the per-platform cards
const cls = k => ({failed:'bad', archived:'ok', done:'ok', pending:'warn',
                   running:'run', queued:'warn', interrupted:'bad'})[k] || '';
const stat = (label, o) => Object.entries(o || {}).map(([k, v]) =>
  `<dt>${label} ${k}</dt><dd class="${cls(k)}">${v.toLocaleString()}</dd>`).join('');

// Schedule controls are excluded: they commit on change, so the server is their truth.
let busy = false;
const snapshot = () => Object.fromEntries([...document.querySelectorAll('#grid [id^=run-]')]
  .map(el => [el.id, el.type === 'checkbox' ? el.checked : el.value]));
function restore(state) {
  for (const [id, value] of Object.entries(state)) {
    const el = document.getElementById(id);
    if (!el) continue;
    if (el.type === 'checkbox') el.checked = value; else el.value = value;
  }
}

function scheduleBlock(p) {
  const boxes = p.categories.map(c => `<label><input type="checkbox" data-sched="${p.platform}" value="${c}"
    ${p.scheduled_categories.includes(c) ? 'checked' : ''}
    onchange="saveSchedule('${p.platform}', ${p.scheduled})"> ${c}</label>`).join('');
  return `<section class="block"><h4>Schedule</h4>
    <fieldset><legend>Categories archived on the timer</legend>${boxes || '<span class="empty">none</span>'}</fieldset>
    <div class="row">
      <span class="hint">${p.scheduled ? `next run ${p.next_run}` : 'paused, the timer will not fire'}</span>
      <button onclick="saveSchedule('${p.platform}', ${!p.scheduled})">${p.scheduled ? 'Pause' : 'Resume'}</button>
    </div></section>`;
}

function jobRow(p, job) {
  const f = FLAGS[job], key = `${p.platform}-${job}`;
  return `<div class="job"><b>${job}</b><span class="controls">
    ${f.includes('category') ? `<select id="run-c-${key}" aria-label="${job} category">
      <option value="">all categories</option>
      ${p.categories.map(c => `<option>${c}</option>`).join('')}</select>` : ''}
    ${f.includes('history') ? `<label><input type="checkbox" id="run-h-${key}"> history</label>` : ''}
    ${f.includes('retry_failed') ? `<label><input type="checkbox" id="run-r-${key}"> retry failed</label>` : ''}
    </span><button onclick="run('${p.platform}','${job}')">Queue</button></div>`;
}

function card(p) {
  const stats = p.error
    ? `<p class="empty">${p.error}</p>`
    : `<dl class="stats"><dt>items</dt><dd>${p.total.toLocaleString()}</dd>
       ${stat('archive', p.archive)}${stat('upload', p.upload)}${stat('embed', p.embed)}
       ${p.resumable.length ? `<dt>resumable walk</dt><dd class="warn">${p.resumable.join(', ')}</dd>` : ''}</dl>`;
  return `<article class="card">
    <div class="card-head"><h3>${p.platform}</h3>
      <span class="pill ${p.scheduled ? 'on' : 'off'}">${p.scheduled ? 'scheduled ' + p.next_run : 'paused'}</span>
    </div>
    ${stats}
    ${scheduleBlock(p)}
    <section class="block"><h4>Run now</h4>
      ${Object.keys(FLAGS).map(j => jobRow(p, j)).join('')}
      <div class="job"><b>cycle</b>
        <span class="controls hint">the scheduled categories, then upload and embed</span>
        <button class="primary" onclick="runCycle('${p.platform}')">Queue</button></div>
    </section></article>`;
}

const jobCells = (j, when) =>
  `<td>${j.platform}</td><td>${j.job}${j.flags ? ' ' + j.flags : ''}</td>
   <td class="${cls(j.status)}">${j.status}</td><td>${j.source}</td><td>${when || ''}</td>`;

async function load() {
  if (busy) return;
  const d = await (await fetch('/api/status')).json();
  const hours = (d.interval_minutes / 60).toFixed(0);
  document.getElementById('interval').textContent =
    `Scheduled platforms queue a cycle every ${d.interval_minutes >= 60 ? hours + 'h' : d.interval_minutes + 'm'}.`;

  const state = snapshot();
  document.getElementById('grid').innerHTML = d.platforms.map(card).join('');
  restore(state);

  const sources = await (await fetch('/api/sources')).json();
  document.getElementById('sources').innerHTML = sourcesBlock(sources);

  document.getElementById('active').innerHTML =
    `<tr><th>platform</th><th>job</th><th>status</th><th>source</th><th>queued</th><th></th></tr>` +
    (d.active.map(j => `<tr>${jobCells(j, j.queued_at)}<td>${j.status === 'queued'
      ? `<button class="link" onclick="cancel(${j.id})">cancel</button>` : ''}</td></tr>`).join('')
     || '<tr><td class="empty" colspan="6">nothing queued</td></tr>');

  document.getElementById('recent').innerHTML =
    `<tr><th>platform</th><th>job</th><th>status</th><th>source</th><th>finished</th><th>error</th></tr>` +
    (d.recent.map(j => `<tr>${jobCells(j, j.finished_at)}
      <td class="err" title="${(j.error || '').replace(/"/g, '&quot;')}">${
        (j.error || '').split('\\n').filter(Boolean).pop()?.trim().slice(0, 80) || ''}</td></tr>`).join('')
     || '<tr><td class="empty" colspan="6">nothing yet</td></tr>');
}

function sourcesBlock(d) {
  const platforms = Object.entries(d.kinds).filter(([, kinds]) => kinds.length);
  if (!platforms.length)
    return '<p class="empty">No platform can archive a source yet.</p>';

  const options = platforms.map(([p]) => `<option value="${p}">${p}</option>`).join('');
  const rows = d.sources.map(s => `<tr>
    <td>${s.platform}</td>
    <td><code>${s.kind}:${s.target}</code></td>
    <td class="${s.enabled ? 'ok' : 'warn'}">${s.enabled ? 'syncing' : 'paused'}</td>
    <td>${s.items.toLocaleString()}</td>
    <td class="hint">${s.last_run || 'never'}</td>
    <td>
      <button class="link" onclick="runSource('${s.platform}','${s.kind}','${s.target}',false)">sync</button>
      <button class="link" onclick="runSource('${s.platform}','${s.kind}','${s.target}',true)">full</button>
      <button class="link" onclick="toggleSource('${s.platform}','${s.kind}','${s.target}',${!s.enabled})">${
        s.enabled ? 'pause' : 'resume'}</button>
      <button class="link" onclick="untrack('${s.platform}','${s.kind}','${s.target}')">untrack</button>
    </td></tr>`).join('');

  return `<div class="scroll"><table>
    <tr><th>platform</th><th>source</th><th>state</th><th>items</th><th>last run</th><th></th></tr>
    ${rows || '<tr><td class="empty" colspan="6">nothing tracked</td></tr>'}
  </table></div>
  <section class="block"><h4>Add</h4>
    <div class="row">
      <select id="src-platform">${options}</select>
      <input id="src-target" placeholder="account or subreddit" size="20">
      <button onclick="addSource()">track</button>
      <button class="link" onclick="addSource(true)">run once</button>
    </div>
    <p class="hint">Tracked sources re-sync on their platform's cycle. Running once archives it
       now without tracking it.</p>
  </section>`;
}

const srcBody = (platform, kind, target) => ({platform, kind, target});

function addSource(once) {
  const platform = document.getElementById('src-platform').value;
  const target = document.getElementById('src-target').value.trim();
  if (!target) return;
  document.getElementById('src-target').value = '';
  post(once ? '/api/sources/run' : '/api/sources/add', {platform, target});
}
const runSource = (platform, kind, target, full) =>
  post(`/api/sources/run?full=${full}`, srcBody(platform, kind, target));
const toggleSource = (platform, kind, target, enabled) =>
  post(`/api/sources/enabled?enabled=${enabled}`, srcBody(platform, kind, target));
const untrack = (platform, kind, target) =>
  post('/api/sources/remove', srcBody(platform, kind, target));

async function post(url, body) {
  busy = true;
  try {
    const res = await (await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: body ? JSON.stringify(body) : null})).json();
    const msg = document.getElementById('msg');
    msg.textContent = res.error || res.started;
    msg.classList.toggle('error', Boolean(res.error));
  } finally {
    busy = false;
  }
  load();
}

const run = (platform, job) => {
  const g = id => document.getElementById(id), key = `${platform}-${job}`;
  post('/api/run', {platform, job, category: g(`run-c-${key}`)?.value || null,
    history: g(`run-h-${key}`)?.checked || false, retry_failed: g(`run-r-${key}`)?.checked || false});
};
const runCycle = platform => post(`/api/cycle/${platform}`);
const saveSchedule = (platform, enabled) => post('/api/schedule', {platform, enabled,
  categories: [...document.querySelectorAll(`input[data-sched="${platform}"]:checked`)].map(el => el.value)});
const cancel = id => post(`/api/cancel/${id}`);

load();
setInterval(load, 5000);
</script>
"""
