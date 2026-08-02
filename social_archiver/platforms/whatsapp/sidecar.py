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

_IDLE_POLL = 15
_RESTART_DELAY = 30
# An exit this quick is configuration (bad store, invalidated session), not weather, so the
# retry is slow rather than a hot loop.
_FATAL_EXIT_SECONDS = 10
_FATAL_DELAY = 600

_SOURCE_DIR = Path(__file__).resolve().parents[3] / "wabridge"
_LOG_NAME = "wabridge.log"

_pairing: asyncio.subprocess.Process | None = None


async def run():
    with (config.LOGS_DIR / _LOG_NAME).open("ab") as log:
        clock = asyncio.get_running_loop().time
        while True:
            if not _paired():
                await asyncio.sleep(_IDLE_POLL)
                continue
            binary = await _ensure_binary(log)
            if binary is None:
                logger.error("bridge is paired but there is no wabridge binary and no Go toolchain to build one")
                await asyncio.sleep(_FATAL_DELAY)
                continue

            started = clock()
            process = await asyncio.create_subprocess_exec(
                binary, "-store", str(config.BRIDGE_DIR), "sync", stdout=log, stderr=log
            )
            logger.info(f"wabridge sync running (pid {process.pid}), logging to {_LOG_NAME}")
            try:
                code = await process.wait()
            except asyncio.CancelledError:
                process.terminate()
                await process.wait()
                raise
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
    """Begin pairing: run `wabridge auth` with the QR mirrored to a file. Returns an error
    message, or None when pairing is underway (or already was)."""
    global _pairing
    if _paired():
        return "already paired"
    if _pairing is not None and _pairing.returncode is None:
        return None

    with (config.LOGS_DIR / _LOG_NAME).open("ab") as log:
        binary = await _ensure_binary(log)
        if binary is None:
            return "no wabridge binary and no Go toolchain to build one"
        _qr_file().unlink(missing_ok=True)
        _pairing = await asyncio.create_subprocess_exec(
            binary, "-store", str(config.BRIDGE_DIR), "-qr-file", str(_qr_file()), "auth", stdout=log, stderr=log
        )
    return None


def pair_state() -> dict:
    """What the pairing overlay polls: the current QR text while auth runs, and whether the
    session ended up paired. The sync loop notices a fresh pairing by itself."""
    qr = _qr_file()
    return {
        "paired": _paired(),
        "pairing": _pairing is not None and _pairing.returncode is None,
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
