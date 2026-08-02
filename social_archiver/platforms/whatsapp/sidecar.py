"""The live bridge, run and paired from inside the archiver.

The bridge is Go because the WhatsApp protocol lives in whatsmeow; everything about
operating it lives here so the web UI or daemon stays the only process anyone starts.
Pairing is the on switch: the sidecar idles until the session store holds a device, then
keeps `wabridge sync` running, restarts it if it dies, and stops with the process. The
binary is built on first need when a Go toolchain is present (the Docker image ships it
prebuilt). Pairing itself runs `wabridge auth` with the QR mirrored to a file the web UI
polls and renders.
"""

import asyncio
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from social_archiver.platforms.whatsapp import config

logger = logging.getLogger(__name__)

_IDLE_POLL = 5
_RESTART_DELAY = 30
# An exit this quick is configuration (bad store, invalidated session), not weather, so the
# retry is slow rather than a hot loop.
_FATAL_EXIT_SECONDS = 10
_FATAL_DELAY = 600

_SOURCE_DIR = Path(__file__).resolve().parents[3] / "wabridge"
_LOG_NAME = "wabridge.log"

_pair_requested = asyncio.Event()
_child: asyncio.subprocess.Process | None = None
_child_pairing = False


async def run():
    global _child, _child_pairing
    with (config.LOGS_DIR / _LOG_NAME).open("ab") as log:
        clock = asyncio.get_running_loop().time
        while True:
            pairing = not _paired() and _pair_requested.is_set()
            if not (_paired() or pairing):
                await asyncio.sleep(_IDLE_POLL)
                continue
            binary = await _ensure_binary(log)
            if binary is None:
                logger.error("no wabridge binary and no Go toolchain to build one")
                await asyncio.sleep(_FATAL_DELAY)
                continue

            args = [binary, "-store", str(config.BRIDGE_DIR)]
            if pairing:
                # The same process pairs and then keeps running as the sync: a pair-then-exit
                # handoff loses the link (the phone rolls back a device that disconnects
                # right after scanning) and the history push that follows it.
                _pair_requested.clear()
                _qr_file().unlink(missing_ok=True)
                args += ["-pair", "-qr-file", str(_qr_file())]
            args.append("sync")

            started = clock()
            _child = await asyncio.create_subprocess_exec(*args, stdout=log, stderr=log)
            _child_pairing = pairing
            logger.info(f"wabridge sync running (pid {_child.pid}{', pairing' if pairing else ''})")
            try:
                code = await _child.wait()
            except asyncio.CancelledError:
                _child.terminate()
                await _child.wait()
                raise
            if pairing and not _paired():
                logger.warning(f"pairing ended without success (exit {code}); press Pair to retry")
                continue
            delay = _FATAL_DELAY if clock() - started < _FATAL_EXIT_SECONDS else _RESTART_DELAY
            logger.warning(f"wabridge sync exited with code {code}; restarting in {delay}s")
            await asyncio.sleep(delay)


def status() -> str | None:
    """One line for the platform card, read in the same thread scan as the item counts."""
    if not _paired():
        return "not paired"
    mirror = config.BRIDGE_DIR / "wabridge.db"
    if not mirror.exists():
        return "paired, no mirror yet"

    db = sqlite3.connect(f"file:{mirror}?mode=ro", uri=True)
    try:
        newest = db.execute("SELECT max(ts) FROM messages").fetchone()[0]
        pending = db.execute(
            "SELECT count(*) FROM messages WHERE direct_path IS NOT NULL AND local_path IS NULL AND media_error IS NULL"
        ).fetchone()[0]
    finally:
        db.close()
    if newest is None:
        return "paired, waiting for the first message"
    line = f"last message {_age(newest)} ago"
    return f"{line}, {pending} media pending" if pending else line


async def pair_start() -> str | None:
    """Ask the supervisor to start a pairing sync. Returns an error message, or None when
    pairing is underway (or already was)."""
    if _paired():
        return "already paired"
    with (config.LOGS_DIR / _LOG_NAME).open("ab") as log:
        if await _ensure_binary(log) is None:
            return "no wabridge binary and no Go toolchain to build one"
    _pair_requested.set()
    return None


def pair_state() -> dict:
    """What the pairing overlay polls: the current QR text while pairing runs, and whether
    the session ended up paired. On success the same process simply continues as the sync."""
    paired = _paired()
    qr = _qr_file()
    return {
        "paired": paired,
        "pairing": not paired
        and (_pair_requested.is_set() or (_child_pairing and _child is not None and _child.returncode is None)),
        "qr": qr.read_text() if qr.exists() else None,
    }


async def _ensure_binary(log) -> str | None:
    if override := os.getenv("WABRIDGE_BIN"):
        return override
    if found := shutil.which("wabridge"):
        return found
    local = _SOURCE_DIR / "wabridge"
    if local.exists():
        return str(local)
    if not (_SOURCE_DIR / "go.mod").exists() or shutil.which("go") is None:
        return None

    logger.info("building wabridge (first use)")
    process = await asyncio.create_subprocess_exec(
        "go", "build", "-o", "wabridge", ".", cwd=_SOURCE_DIR, stdout=log, stderr=log
    )
    if await process.wait() != 0:
        logger.error(f"wabridge build failed; see {_LOG_NAME}")
        return None
    return str(local)


def _qr_file() -> Path:
    return config.BRIDGE_DIR / "qr.txt"


def _paired() -> bool:
    """Paired means whatsmeow holds a device row, not merely that the session file exists."""
    session = config.BRIDGE_DIR / "session.db"
    if not session.exists():
        return False
    db = sqlite3.connect(f"file:{session}?mode=ro", uri=True)
    try:
        return db.execute("SELECT count(*) FROM whatsmeow_device").fetchone()[0] > 0
    except sqlite3.Error:
        return False
    finally:
        db.close()


def _age(ts: int) -> str:
    seconds = int((datetime.now(timezone.utc) - datetime.fromtimestamp(ts, tz=timezone.utc)).total_seconds())
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 7200:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"
