"""Reading Instagram accounts off their posts, fifty to a `media/infos/` request.

A post names its author's handle, name, avatar and badges but no bio or counts, and the
archive makes no profile lookups on Instagram, so this is all an account will show.
"""

import asyncio

from social_archiver.core.profile_refresh import AuthorReading
from social_archiver.core.profile_store import UnobservedProfile
from social_archiver.core.profiles import ProfileSnapshot
from social_archiver.platforms.instagram.client import InstagramClient
from social_archiver.platforms.instagram.fetchers.media_infos import MEDIA_INFO_BATCH, resolve_media
from social_archiver.platforms.instagram.simple_media import SimpleUser


class PostAuthorReader:
    platform = "instagram"
    batch = MEDIA_INFO_BATCH

    def __init__(self, ig_client: InstagramClient):
        self.ig_client = ig_client

    async def read(self, item_ids: list[str]) -> AuthorReading:
        # instagrapi is synchronous; inline it would block every other platform's work
        posts = await asyncio.to_thread(resolve_media, self.ig_client, item_ids)
        return AuthorReading([s for post in posts if (s := SimpleUser.from_dict(post.get("user", {})).snapshot)])

    def refused(self, account: UnobservedProfile, reason: str | None) -> ProfileSnapshot:
        """Instagram drops an unreadable post from the response without saying why."""
        return ProfileSnapshot(account.profile_id, account.handle)
