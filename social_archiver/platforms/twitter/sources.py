"""Archiving an X account in full.

Measured against the real API before this was written (see X_RESEARCH.md):

- `UserTweets` serves a fixed 20 a page on a 50-per-15-minutes bucket, and its guest-token
  form returns one page with no cursor at all. Too slow authed, too shallow as a guest.
- `from:user` on SearchTimeline slices with `max_id:` exactly as a conversation does, at ~18
  tweets per request, and reaches back to at least 2019.

So the walk is search, sliced. The one rule that does not carry over from conversation walking
is when to stop: there, a slice returning under the cap proves exhaustion, because an author's
tweets within a single conversation are bounded. Walking a whole account, slices come back
short constantly while thousands more sit below — measured on @ArmchairW, whose walk returned
13, 18, 18, 19 and kept finding new tweets throughout. Only a slice that yields nothing new
ends it.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence

from social_archiver.core.database import Item
from social_archiver.core.sources import SourceRef
from social_archiver.platforms.twitter.client import SEARCH_RESULT_CAP, TwitterClient
from social_archiver.platforms.twitter.simple_tweet import SimpleTweet

logger = logging.getLogger(__name__)

PAGE_DELAY = 1.5

# A walk long enough to hit this is one where slicing has stopped converging; stop rather than
# spend a bucket forever. At ~40 tweets a slice this is a quarter of a million tweets.
MAX_SLICES = 6000


class TwitterSourceFetcher:
    kinds = ("profile",)

    def __init__(self, tw_client: TwitterClient):
        self.tw_client = tw_client

    def category(self, ref: SourceRef) -> str:
        """Each account gets its own category, so its media lands in its own folder and it can
        be uploaded or embedded independently of the others."""
        return f"profile-{ref.target.lower()}"

    async def walk(self, ref: SourceRef, since: str | None) -> AsyncIterator[Sequence[Item]]:
        base = f"from:{ref.target}"
        query = base
        seen: set[str] = set()
        known: dict[str, dict] = {}  # every tweet seen so far, for local thread reconstruction
        category = self.category(ref)
        floor = int(since) if since else None

        for slice_number in range(MAX_SLICES):
            found = await self.tw_client.search_tweets(query, product="Latest", page_delay=PAGE_DELAY)
            fresh = [t for t in found if t["id"] not in seen]
            if not fresh:
                logger.info(f"{ref}: slice {slice_number + 1} added nothing, walk complete")
                return

            seen.update(t["id"] for t in found)
            oldest = min(int(t["id"]) for t in found)

            # Ordered newest first so a batch's own replies are already in `known`, which is
            # what lets thread position be settled without another request.
            fresh.sort(key=lambda t: int(t["id"]), reverse=True)
            for tweet in fresh:
                known[tweet["id"]] = tweet

            wanted = [t for t in fresh if floor is None or int(t["id"]) > floor]
            if wanted:
                yield [self._to_item(t, known, category) for t in wanted]

            if floor is not None and oldest <= floor:
                logger.info(f"{ref}: reached the newest item already held, delta walk complete")
                return
            if len(found) < SEARCH_RESULT_CAP and not wanted:
                # Nothing new and nothing left below: only reachable on a delta walk.
                return

            query = f"{base} max_id:{oldest - 1}"
            if PAGE_DELAY:
                await asyncio.sleep(PAGE_DELAY)

        logger.warning(f"{ref}: stopped at MAX_SLICES={MAX_SLICES} with the walk still yielding")

    def _to_item(self, raw: dict, known: dict[str, dict], category: str) -> Item:
        tweet = SimpleTweet.from_api_dict(raw)
        tweet.origin = "profile"
        self._set_thread_metadata(tweet, raw, known)
        return tweet.to_item(category)

    @staticmethod
    def _set_thread_metadata(tweet: SimpleTweet, raw: dict, known: dict[str, dict]):
        """Reconstructed from what the walk already returned, at no request cost.

        A self-reply is newer than the tweet it answers, and the walk runs newest first, so by
        the time a tweet is emitted every reply to it has been seen."""
        conversation_id = raw.get("conversation_id")
        if not conversation_id:
            tweet.thread_position = "standalone"
            return

        author = raw.get("author_username")
        has_self_replies = any(
            other.get("in_reply_to_status_id") == raw["id"] and other.get("author_username") == author
            for other in known.values()
        )
        is_root = not raw.get("in_reply_to_status_id")

        tweet.thread_position = (
            ("root" if has_self_replies else "standalone") if is_root else ("middle" if has_self_replies else "end")
        )
        tweet.has_self_replies = has_self_replies
        tweet.thread_root_id = conversation_id
