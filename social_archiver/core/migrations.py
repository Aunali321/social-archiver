"""Schema evolution for the sqlite files.

`CREATE TABLE IF NOT EXISTS` builds a fresh database correctly and then never looks at an
existing one again, so anything beyond adding a whole new table diverges the moment a schema
changes: the code expects a column the file does not have, and nothing says so until a write
fails. `PRAGMA user_version` records how far a file has been brought forward, and each step
runs once, in order.

A step and the version bump that records it commit together, so a process that dies mid-way
leaves the file on the old version with none of the step applied. That is what lets a step
contain a statement like `ALTER TABLE`, which sqlite cannot express conditionally and which
would fail if it were ever replayed.

The baseline is the exception: a file that predates this mechanism reports version 0 and
replays it, so it alone has to tolerate already being satisfied.

Anything with rows in it is copied before a step runs. A migration is the one operation that
can lose an archive rather than just fail, and `VACUUM INTO` takes a consistent copy of a live
WAL database, which a file copy does not.
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

Migration = Sequence[str]

BACKUPS_KEPT = 5


async def apply(connection: aiosqlite.Connection, migrations: Sequence[Migration], name: str, db_path: Path):
    cursor = await connection.execute("PRAGMA user_version")
    version = (await cursor.fetchone())[0]

    if version > len(migrations):
        raise RuntimeError(
            f"{name} is at schema version {version} but this build only knows {len(migrations)}; "
            "it was written by a newer version, and running an older one against it would corrupt it"
        )
    if version == len(migrations):
        return

    if await _has_content(connection):
        await _backup(connection, db_path, name, version)

    for index in range(version, len(migrations)):
        await connection.execute("BEGIN")
        try:
            for statement in migrations[index]:
                await connection.execute(statement)
            # Inside the same transaction as the statements it records, so the two cannot
            # disagree. PRAGMA takes no parameters, hence interpolating an int we own.
            await connection.execute(f"PRAGMA user_version = {index + 1}")
        except Exception:
            await connection.rollback()
            raise
        await connection.commit()
        if index:
            logger.info(f"{name}: applied schema migration {index + 1}")


async def _has_content(connection: aiosqlite.Connection) -> bool:
    """A file with no tables is one this process is creating, and copying it protects nothing."""
    cursor = await connection.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'")
    return (await cursor.fetchone())[0] > 0


async def _backup(connection: aiosqlite.Connection, db_path: Path, name: str, version: int):
    folder = db_path.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = folder / f"{db_path.stem}-v{version}-{stamp}.db"

    # VACUUM INTO reads through the WAL, so the copy is a consistent snapshot rather than
    # whatever happened to be flushed to the main file.
    await connection.execute(f"VACUUM INTO '{target}'")
    logger.info(f"{name}: backed up to {target} before migrating from version {version}")

    stale = sorted(folder.glob(f"{db_path.stem}-v*.db"))[:-BACKUPS_KEPT]
    for old in stale:
        old.unlink()
        logger.info(f"{name}: removed old backup {old.name}")
