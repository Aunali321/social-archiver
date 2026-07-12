"""Recursive tweet expander.

Given a set of seed tweets (likes or bookmarks), recursively expands:
1. Self-reply chains (full thread, both directions)
2. Parent chain to conversation root
3. Quoted tweets (recursive)
4. Tweets linked by URL in the text (recursive)
5. Seed replies to any tweet in the expanded set
6. Retweets -> expand the original tweet

Uses a conversation cache to minimize API calls: one TweetDetail call per
conversation_id, re-fetched only when a later focal tweet's branch is missing.
Unavailable tweets (deleted, protected, suspended) become tombstone records
instead of silent gaps.
"""
import logging
from datetime import datetime, timezone
from typing import Any

from social_archiver.platforms.twitter.client import TwitterClient
from social_archiver.platforms.twitter.simple_tweet import SimpleTweet

logger = logging.getLogger(__name__)


def datetime_min() -> datetime:
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _is_rate_limit(e: Exception) -> bool:
    msg = str(e).lower()
    return "429" in msg or "rate" in msg


class TweetExpander:
    """Expands seed tweets into full linked sets.

    1. Pre-fetch all seeds into a set (seed_ids).
    2. For each seed tweet, fetch its conversation via TweetDetail, cached by
       conversation_id so the same conversation is never re-fetched.
    3. From the conversation, extract the full self-reply chain, all ancestors
       (walking in_reply_to_status_id to root), and each ancestor's self-reply chain.
    4. Recursively expand quoted and URL-linked tweets with the same rules.
    5. For every tweet in the expanded set, pull in any seed replies.
    6. Retweets: pull in and expand the original tweet.
    7. Repeat (queue-driven) until no new tweets are discovered.
    """

    def __init__(self, tw_client: TwitterClient, page_delay: float = 1.5, seed_origin: str = "liked"):
        self.tw_client = tw_client
        self.page_delay = page_delay
        self.seed_origin = seed_origin

        self._conversation_cache: dict[str, list[dict[str, Any]]] = {}
        self._tweet_cache: dict[str, dict[str, Any]] = {}
        self._tombstones: dict[str, str] = {}
        self._expanded_ids: set[str] = set()
        self._queue: list[str] = []
        self._seed_ids: set[str] = set()
        self._api_calls = 0

    async def expand(self, seed_tweets: list[dict[str, Any]]) -> list[SimpleTweet]:
        """Entry point: expands raw seed tweet dicts (from get_all_likes /
        get_all_bookmarks) into SimpleTweets with origin and linking fields set."""
        for tweet in seed_tweets:
            if tid := tweet.get("id"):
                self._seed_ids.add(tid)
                self._tweet_cache[tid] = tweet

        logger.info(f"Starting expansion of {len(self._seed_ids)} seed tweets (origin={self.seed_origin})")

        for tid in self._seed_ids:
            self._enqueue(tid)

        while self._queue:
            await self._expand_tweet(self._queue.pop(0))

        tombstoned = sum(1 for tid in self._expanded_ids if tid in self._tombstones and tid not in self._tweet_cache)
        logger.info(
            f"Expansion complete: {len(self._expanded_ids)} tweets total "
            f"({len(self._seed_ids)} seeds, {len(self._expanded_ids) - len(self._seed_ids)} discovered, "
            f"{tombstoned} tombstoned), {self._api_calls} API calls"
        )

        return self._build_result()

    def _enqueue(self, tweet_id: str):
        if tweet_id not in self._expanded_ids:
            self._expanded_ids.add(tweet_id)
            self._queue.append(tweet_id)

    async def _expand_tweet(self, tweet_id: str):
        tweet = self._tweet_cache.get(tweet_id)
        if not tweet:
            if tweet_id in self._tombstones:
                return  # already known unavailable, don't burn an API call
            try:
                await self._fetch_conversation_for_tweet(tweet_id)
            except Exception as e:
                if _is_rate_limit(e):
                    raise
                logger.warning(f"Tweet {tweet_id} unavailable, recording tombstone: {e}")
                self._tombstones[tweet_id] = str(e)
                return
            tweet = self._tweet_cache.get(tweet_id)
            if not tweet:
                logger.warning(f"Tweet {tweet_id} not returned by API, recording tombstone")
                self._tombstones.setdefault(tweet_id, "not returned by API")
                return

        try:
            if conversation_id := tweet.get("conversation_id"):
                await self._fetch_conversation(conversation_id, tweet_id)
            else:
                await self._fetch_conversation_for_tweet(tweet_id)
        except Exception as e:
            if _is_rate_limit(e):
                raise
            # The tweet itself is known; losing its conversation context only
            # limits thread/parent expansion, so continue with what we have.
            logger.error(f"Conversation fetch failed for {tweet_id}, expanding without thread context: {e}")

        # the fetch may have replaced a shallow quote-card stub with full TweetDetail data
        tweet = self._tweet_cache.get(tweet_id, tweet)

        self._expand_author_chain(tweet)
        self._expand_parent_chain(tweet)
        self._expand_quoted_tweet(tweet)
        self._expand_linked_tweets(tweet)
        self._expand_retweet(tweet)
        self._check_seed_replies(tweet)

    async def _fetch_conversation(self, conversation_id: str, focal_tweet_id: str):
        cached = self._conversation_cache.get(conversation_id)
        if cached is not None and any(t["id"] == focal_tweet_id for t in cached):
            return
        if cached is not None:
            logger.debug(f"Conversation {conversation_id} cached but missing branch of {focal_tweet_id}, re-fetching")

        logger.debug(f"Fetching conversation {conversation_id} (focal: {focal_tweet_id})")
        self._api_calls += 1
        result = await self.tw_client.get_thread(focal_tweet_id, page_delay=self.page_delay)
        self._merge_thread_result(conversation_id, result.tweets, result.tombstones)
        logger.debug(f"Conversation {conversation_id}: {len(result.tweets)} tweets cached")

    async def _fetch_conversation_for_tweet(self, tweet_id: str):
        """Fetch conversation using a tweet ID when the conversation_id isn't known yet."""
        logger.debug(f"Fetching tweet detail for {tweet_id}")
        self._api_calls += 1
        result = await self.tw_client.get_thread(tweet_id, page_delay=self.page_delay)

        conv_id = next((t.get("conversation_id") for t in result.tweets if t.get("id") == tweet_id), None)
        if not conv_id and result.tweets:
            conv_id = result.tweets[0].get("conversation_id")

        self._merge_thread_result(conv_id, result.tweets, result.tombstones)

    def _merge_thread_result(self, conversation_id: str | None, tweets: list[dict[str, Any]], tombstones: dict[str, str]):
        if conversation_id:
            cached = self._conversation_cache.setdefault(conversation_id, [])
            existing_ids = {t["id"] for t in cached}
            cached.extend(t for t in tweets if t["id"] not in existing_ids)

        # TweetDetail data overwrites cached entries: timeline payloads nest quotes
        # only one level deep, so a quote-card stub cached earlier is missing its own
        # quoted_tweet_id and would break quote-of-quote chains.
        for tweet in tweets:
            if tid := tweet.get("id"):
                self._tweet_cache[tid] = tweet

        self._tombstones.update(tombstones)

    def _expand_author_chain(self, tweet: dict[str, Any]):
        """Find all self-replies by the same author connected to this tweet, both directions."""
        conversation_id = tweet.get("conversation_id")
        if not conversation_id:
            return

        conv_tweets = self._conversation_cache.get(conversation_id, [])
        if not conv_tweets:
            return

        author = tweet.get("author_username")
        if not author:
            return

        by_id = {t["id"]: t for t in conv_tweets}
        chain_ids = set()

        current = tweet
        while current and current.get("author_username") == author:
            chain_ids.add(current["id"])
            parent = by_id.get(current.get("in_reply_to_status_id"))
            if not parent or parent.get("author_username") != author:
                break
            current = parent

        changed = True
        while changed:
            changed = False
            for t in conv_tweets:
                if t.get("author_username") != author or t["id"] in chain_ids:
                    continue
                if t.get("in_reply_to_status_id") in chain_ids:
                    chain_ids.add(t["id"])
                    changed = True

        for tid in chain_ids:
            if tid not in self._expanded_ids and self._tweet_cache.get(tid):
                self._enqueue(tid)

    def _expand_parent_chain(self, tweet: dict[str, Any]):
        """Walk up the reply chain to the conversation root."""
        conversation_id = tweet.get("conversation_id")
        if not conversation_id:
            return

        by_id = {t["id"]: t for t in self._conversation_cache.get(conversation_id, [])}

        current = tweet
        while current:
            parent_id = current.get("in_reply_to_status_id")
            if not parent_id:
                break

            self._enqueue(parent_id)

            parent = by_id.get(parent_id)
            if not parent:
                break
            self._expand_author_chain(parent)
            current = parent

    def _expand_quoted_tweet(self, tweet: dict[str, Any]):
        quoted = tweet.get("quoted_tweet")
        quoted_id = tweet.get("quoted_tweet_id")

        if quoted:
            if qid := quoted.get("id"):
                self._tweet_cache.setdefault(qid, quoted)
                self._enqueue(qid)
        elif quoted_id:
            self._enqueue(quoted_id)

    def _expand_linked_tweets(self, tweet: dict[str, Any]):
        for linked_id in tweet.get("linked_tweet_ids") or []:
            self._enqueue(linked_id)

    def _expand_retweet(self, tweet: dict[str, Any]):
        if tweet.get("is_retweet") and (retweeted_id := tweet.get("retweeted_tweet_id")):
            self._enqueue(retweeted_id)

    def _check_seed_replies(self, tweet: dict[str, Any]):
        """Pull in any replies to this tweet, within its conversation, that the user liked/bookmarked."""
        conversation_id = tweet.get("conversation_id")
        if not conversation_id:
            return

        for t in self._conversation_cache.get(conversation_id, []):
            tid = t.get("id")
            if tid and t.get("in_reply_to_status_id") == tweet["id"] and tid in self._seed_ids:
                self._enqueue(tid)

    def _build_result(self) -> list[SimpleTweet]:
        result = []

        for tweet_id in self._expanded_ids:
            tweet_data = self._tweet_cache.get(tweet_id)
            if not tweet_data:
                result.append(self._build_tombstone(tweet_id))
                continue

            simple = SimpleTweet.from_api_dict(tweet_data)
            simple.origin = self.seed_origin if tweet_id in self._seed_ids else self._determine_origin(tweet_data)
            if tweet_id not in self._seed_ids:
                simple.discovered_via_tweet_id = self._find_discovered_via(tweet_data)
            self._set_thread_metadata(simple, tweet_data)

            result.append(simple)

        result.sort(key=lambda t: t.created_at or datetime_min())
        return result

    def _build_tombstone(self, tweet_id: str) -> SimpleTweet:
        stub = {"id": tweet_id}
        simple = SimpleTweet(
            id=tweet_id,
            text=self._tombstones.get(tweet_id, "unavailable"),
            author_username="unknown",
            author_name="unknown",
            is_tombstone=True,
        )
        simple.origin = self.seed_origin if tweet_id in self._seed_ids else self._determine_origin(stub)
        simple.discovered_via_tweet_id = self._find_discovered_via(stub)
        return simple

    def _determine_origin(self, tweet: dict[str, Any]) -> str:
        tid = tweet.get("id")

        if tid in self._seed_ids:
            return self.seed_origin

        for expanded_id in self._expanded_ids:
            cached = self._tweet_cache.get(expanded_id)
            if cached and cached.get("quoted_tweet_id") == tid:
                return "quoted"

        for expanded_id in self._expanded_ids:
            cached = self._tweet_cache.get(expanded_id)
            if cached and cached.get("retweeted_tweet_id") == tid:
                return "retweet"

        for expanded_id in self._expanded_ids:
            cached = self._tweet_cache.get(expanded_id)
            if cached and tid in (cached.get("linked_tweet_ids") or []):
                return "linked"

        for expanded_id in self._expanded_ids:
            cached = self._tweet_cache.get(expanded_id)
            if cached and cached.get("in_reply_to_status_id") == tid:
                if cached.get("author_username") != tweet.get("author_username"):
                    return "parent"

        conversation_id = tweet.get("conversation_id")
        if conversation_id:
            for seed_id in self._seed_ids:
                seed = self._tweet_cache.get(seed_id)
                if (
                    seed
                    and seed.get("conversation_id") == conversation_id
                    and seed.get("author_username") == tweet.get("author_username")
                ):
                    return "thread"

        return "thread"  # default: part of a thread expansion

    def _find_discovered_via(self, tweet: dict[str, Any], _seen: set[str] | None = None) -> str | None:
        """Find which seed tweet led to discovering this tweet."""
        seen = _seen or set()
        tid = tweet.get("id")
        if tid in seen:
            return None
        seen.add(tid)

        conversation_id = tweet.get("conversation_id")
        if conversation_id:
            for seed_id in self._seed_ids:
                seed = self._tweet_cache.get(seed_id)
                if seed and seed.get("conversation_id") == conversation_id:
                    return seed_id

        for expanded_id in self._expanded_ids:
            cached = self._tweet_cache.get(expanded_id)
            if not cached:
                continue
            if cached.get("quoted_tweet_id") == tid or tid in (cached.get("linked_tweet_ids") or []):
                if expanded_id in self._seed_ids:
                    return expanded_id
                return self._find_discovered_via(cached, seen)

        return None

    def _set_thread_metadata(self, simple: SimpleTweet, tweet_data: dict[str, Any]):
        conversation_id = tweet_data.get("conversation_id")
        if not conversation_id:
            simple.thread_position = "standalone"
            return

        conv_tweets = self._conversation_cache.get(conversation_id, [])
        author = tweet_data.get("author_username")

        has_self_replies = any(
            t.get("in_reply_to_status_id") == tweet_data["id"] and t.get("author_username") == author
            for t in conv_tweets
        )
        is_root = not tweet_data.get("in_reply_to_status_id")

        if is_root:
            position = "root" if has_self_replies else "standalone"
        else:
            position = "middle" if has_self_replies else "end"

        simple.thread_position = position
        simple.has_self_replies = has_self_replies
        simple.thread_root_id = conversation_id
