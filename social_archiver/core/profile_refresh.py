"""Describing the accounts the archive holds only by name.

A post's payload carries its author, so an account is read off one of its own archived posts,
as many to a request as the platform's batch lookup allows. A post the platform refuses means
trying the account's next one. An account with nothing left to read is recorded as whatever the
last refusal said, which is also what stops it being asked about again.
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

from social_archiver.core.database import Database
from social_archiver.core.profile_store import UnobservedProfile
from social_archiver.core.profiles import ProfileSnapshot

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AuthorReading:
    authors: list[ProfileSnapshot] = field(default_factory=list)
    refusals: dict[str, str] = field(default_factory=dict)  # item_id -> the platform's reason


class AuthorReader(Protocol):
    platform: str
    batch: int  # posts one lookup request resolves

    async def read(self, item_ids: list[str]) -> AuthorReading: ...

    def refused(self, account: UnobservedProfile, reason: str | None) -> ProfileSnapshot:
        """What a refusal says about the account itself, as opposed to the one post."""
        ...


async def refresh_profiles(db: Database, reader: AuthorReader):
    queue = deque(await db.profiles.unobserved())
    logger.info(f"{len(queue)} account(s) never described; reading each off one of its posts")
    described = unreadable = 0
    while queue:
        batch = [queue.popleft() for _ in range(min(reader.batch, len(queue)))]
        asked = {account.item_ids.pop(0): account for account in batch}
        reading = await reader.read(list(asked))

        snapshots = list(reading.authors)
        read = {snapshot.profile_id for snapshot in snapshots}
        for item_id, account in asked.items():
            if account.profile_id in read:
                continue
            if account.item_ids:
                queue.append(account)
            else:
                snapshots.append(reader.refused(account, reading.refusals.get(item_id)))
                unreadable += 1
        await db.profiles.observe(reader.platform, snapshots)
        described += len(read & {account.profile_id for account in batch})
        logger.info(f"Profiles: {described} described, {unreadable} unreadable, {len(queue)} to go")
