"""Optional web UI: archive status and one-off job runs.

The archiver does not need this — the daemon covers the routine path and the CLI covers
everything. It exists for hosts like a NAS, where the app manager gives you start/stop for a
whole stack but no way to see how far a walk got or to run one job for one platform.

Every job and flag the CLI exposes is here, and nothing that it does not.
Serves on the LAN with no authentication, so keep it off the public internet.
"""

import asyncio
import importlib
import sqlite3
from dataclasses import dataclass, field

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from social_archiver.core import config

PLATFORMS = ("instagram", "reddit", "twitter")

# Which flags each job accepts, mirroring core.cli.build_parser
JOB_FLAGS = {
    "archive": ("category", "history", "retry_failed"),
    "upload": ("retry_failed",),
    "embed": ("retry_failed",),
    "run": ("history",),
}

app = FastAPI(title="Social Archiver")

# One job per platform at a time: they contend for the same rows and the same rate limits.
_running: dict[str, str] = {}


@dataclass(slots=True)
class PlatformStatus:
    platform: str
    categories: list[str] = field(default_factory=list)
    total: int = 0
    archive: dict[str, int] = field(default_factory=dict)
    upload: dict[str, int] = field(default_factory=dict)
    embed: dict[str, int] = field(default_factory=dict)
    resumable: list[str] = field(default_factory=list)
    running: str | None = None
    error: str | None = None


class RunRequest(BaseModel):
    platform: str
    job: str
    history: bool = False
    retry_failed: bool = False
    category: str | None = None


def _categories(platform: str) -> list[str]:
    archiver = importlib.import_module(f"social_archiver.platforms.{platform}.archiver")
    # Twitter models a category as an object pairing its name with a seed origin
    return [getattr(c, "name", c) for c in archiver.CATEGORIES]


def _counts(db: sqlite3.Connection, column: str) -> dict[str, int]:
    return {row[0]: row[1] for row in db.execute(f"SELECT {column}, count(*) FROM items GROUP BY 1")}


def _status(platform: str) -> PlatformStatus:
    status = PlatformStatus(platform=platform, running=_running.get(platform), categories=_categories(platform))
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


async def _run(request: RunRequest):
    entry = importlib.import_module(f"social_archiver.platforms.{request.platform}.__main__")
    # `run` is run_all in the module; the CLI name is what the UI shows
    job = getattr(entry, "run_all" if request.job == "run" else request.job)
    flags = JOB_FLAGS[request.job]
    kwargs: dict[str, object] = {}
    if "history" in flags:
        kwargs["fetch_all"] = request.history
    if "category" in flags:
        kwargs["category"] = request.category or None
    if "retry_failed" in flags:
        kwargs["retry_failed"] = request.retry_failed
    try:
        await job(**kwargs)
    finally:
        _running.pop(request.platform, None)


@app.get("/api/status")
def status() -> list[PlatformStatus]:
    return [_status(platform) for platform in PLATFORMS]


@app.post("/api/run")
async def run(request: RunRequest) -> dict[str, str]:
    if request.platform not in PLATFORMS or request.job not in JOB_FLAGS:
        return {"error": "unknown platform or job"}
    if request.category and request.category not in _categories(request.platform):
        return {"error": f"{request.platform} has no category {request.category!r}"}
    if busy := _running.get(request.platform):
        return {"error": f"{request.platform} is already running {busy}"}
    _running[request.platform] = request.job
    asyncio.create_task(_run(request))
    return {"started": f"{request.platform} {request.job}"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX


INDEX = """<!doctype html>
<title>Social Archiver</title>
<style>
 :root { color-scheme: dark; --bg:#111418; --card:#191d23; --line:#262c34; --dim:#8b949e; --ok:#3fb950; --warn:#d29922; --bad:#f85149 }
 body { margin:0; padding:2rem; background:var(--bg); color:#e6edf3; font:15px/1.55 ui-sans-serif,system-ui,sans-serif }
 h1 { font-size:1.05rem; font-weight:600; margin:0 0 1.25rem; letter-spacing:.02em }
 .grid { display:grid; gap:1rem; grid-template-columns:repeat(auto-fit,minmax(380px,1fr)); max-width:1300px }
 .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:1.1rem 1.2rem }
 .name { font-weight:600; text-transform:capitalize; margin-bottom:.8rem; display:flex; justify-content:space-between; align-items:center }
 .badge { font-size:.72rem; padding:.15rem .55rem; border-radius:99px; background:#1f6feb33; color:#58a6ff; font-weight:500 }
 table.counts { width:100%; border-collapse:collapse; font-size:.85rem; margin-bottom:.5rem }
 table.counts td { padding:.15rem 0; color:var(--dim) }
 table.counts td+td { text-align:right; color:#e6edf3; font-variant-numeric:tabular-nums }
 .ok{color:var(--ok)} .warn{color:var(--warn)} .bad{color:var(--bad)}
 .job { display:flex; align-items:center; gap:.45rem; padding:.4rem 0; border-top:1px solid var(--line); flex-wrap:wrap }
 .job b { font-weight:500; font-size:.82rem; width:4.2rem; color:#e6edf3 }
 button { background:#21262d; color:#e6edf3; border:1px solid var(--line); border-radius:6px; padding:.3rem .7rem; font-size:.79rem; cursor:pointer; font-family:inherit; margin-left:auto }
 button:hover:not(:disabled) { background:#30363d; border-color:#3d444d }
 button:disabled { opacity:.35; cursor:not-allowed }
 select { background:#21262d; color:#e6edf3; border:1px solid var(--line); border-radius:6px; padding:.25rem .4rem; font-size:.78rem; font-family:inherit }
 label { font-size:.77rem; color:var(--dim); display:inline-flex; align-items:center; gap:.25rem; cursor:pointer; user-select:none }
 #msg { margin-bottom:1rem; min-height:1.2rem; font-size:.85rem; color:var(--dim) }
</style>
<h1>Social Archiver</h1>
<div id="msg"></div>
<div class="grid" id="grid"></div>
<script>
const FLAGS = {archive:['category','history','retry_failed'], upload:['retry_failed'], embed:['retry_failed'], run:['history']};
const cls = k => k === 'failed' ? 'bad' : (k === 'archived' || k === 'done') ? 'ok' : k === 'pending' ? 'warn' : '';
const counts = (label, o) => Object.entries(o || {}).map(([k, v]) =>
  `<tr><td>${label} ${k}</td><td class="${cls(k)}">${v.toLocaleString()}</td></tr>`).join('');

function jobRow(p, job) {
  const f = FLAGS[job], id = `${p.platform}-${job}`;
  return `<div class="job"><b>${job}</b>
    ${f.includes('category') ? `<select id="c-${id}"><option value="">all categories</option>
      ${p.categories.map(c => `<option>${c}</option>`).join('')}</select>` : ''}
    ${f.includes('history') ? `<label><input type="checkbox" id="h-${id}"> history</label>` : ''}
    ${f.includes('retry_failed') ? `<label><input type="checkbox" id="r-${id}"> retry failed</label>` : ''}
    <button ${p.running ? 'disabled' : ''} onclick="run('${p.platform}','${job}')">run</button></div>`;
}

// The poll re-renders the grid, which would otherwise discard whatever the user has
// selected mid-interaction.
const snapshot = () => Object.fromEntries([...document.querySelectorAll('#grid input,#grid select')]
  .map(el => [el.id, el.type === 'checkbox' ? el.checked : el.value]));

function restore(state) {
  for (const [id, value] of Object.entries(state)) {
    const el = document.getElementById(id);
    if (!el) continue;
    if (el.type === 'checkbox') el.checked = value; else el.value = value;
  }
}

async function load() {
  const rows = await (await fetch('/api/status')).json();
  const state = snapshot();
  document.getElementById('grid').innerHTML = rows.map(p => `
    <div class="card">
      <div class="name">${p.platform}${p.running ? `<span class="badge">running ${p.running}</span>` : ''}</div>
      ${p.error ? `<div style="color:var(--dim);font-size:.85rem;margin-bottom:.4rem">${p.error}</div>` : `
        <table class="counts">
          <tr><td>items</td><td>${p.total.toLocaleString()}</td></tr>
          ${counts('archive', p.archive)}${counts('upload', p.upload)}${counts('embed', p.embed)}
          ${p.resumable.length ? `<tr><td>resumable walk</td><td class="warn">${p.resumable.join(', ')}</td></tr>` : ''}
        </table>`}
      ${Object.keys(FLAGS).map(j => jobRow(p, j)).join('')}
    </div>`).join('');
  restore(state);
}

async function run(platform, job) {
  const g = id => document.getElementById(id);
  const key = `${platform}-${job}`;
  const res = await (await fetch('/api/run', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      platform, job,
      category: g(`c-${key}`)?.value || null,
      history: g(`h-${key}`)?.checked || false,
      retry_failed: g(`r-${key}`)?.checked || false,
    }),
  })).json();
  document.getElementById('msg').textContent = res.error || res.started;
  load();
}

load();
setInterval(load, 5000);
</script>
"""
