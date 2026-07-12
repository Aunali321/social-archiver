"""
Recursive tweet expander.

Given a set of liked tweets, recursively expands:
1. Self-reply chains (full thread, both directions)
2. Parent chain to conversation root
3. Quoted tweets (recursive)
4. Liked replies to any tweet in the expanded set
5. Retweets → expand the original tweet

Uses conversation cache to minimize API calls: one TweetDetail call per
conversation_id, not per tweet.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional, Set

from twitter_archiver.simple_tweet import SimpleTweet
from twitter_archiver.twitter_client import TwitterClient

logger = logging.getLogger(__name__)


class TweetExpander:
    """
    Expands liked tweets into full linked sets.

    Algorithm:
    1. Pre-fetch all likes into a set (liked_ids).
    2. For each liked tweet, fetch its conversation via TweetDetail.
       Cache by conversation_id so we never re-fetch the same conversation.
    3. From the conversation, extract:
       a. Full self-reply chain (author chain, both directions)
       b. All ancestors (walk in_reply_to_status_id to root)
       c. Each ancestor's self-reply chain
    4. For every tweet in the expanded set, check if it has a quoted_tweet.
       If yes, expand that quoted tweet recursively (same rules).
    5. For every tweet in the expanded set, check if any of its replies
       are in liked_ids. If yes, add those liked replies and expand them too.
    6. Retweets: add the original tweet and expand it.
    7. Repeat until no new tweets are discovered.
    """

    def __init__(self, tw_client: TwitterClient, page_delay: float = 1.5):
        self.tw_client = tw_client
        self.page_delay = page_delay

        # Cache: conversation_id -> list of tweet dicts from TweetDetail
        self._conversation_cache: Dict[str, List[Dict[str, Any]]] = {}

        # Cache: tweet_id -> tweet dict (all tweets we've seen)
        self._tweet_cache: Dict[str, Dict[str, Any]] = {}

        # Set of tweet IDs in the final expanded result
        self._expanded_ids: Set[str] = set()

        # Queue of tweet IDs to process
        self._queue: List[str] = []

        # Set of liked tweet IDs (pre-fetched)
        self._liked_ids: Set[str] = set()

        # Track API calls for logging
        self._api_calls = 0

    async def expand_likes(
        self, liked_tweets: List[Dict[str, Any]]
    ) -> List[SimpleTweet]:
        """
        Main entry point. Takes the raw liked tweet dicts from get_all_likes(),
        expands everything recursively, and returns a list of SimpleTweets
        with origin and linking fields set.
        """
        # Index all liked tweets
        for tweet in liked_tweets:
            tid = tweet.get("id")
            if tid:
                self._liked_ids.add(tid)
                self._tweet_cache[tid] = tweet

        logger.info(f"Starting expansion of {len(self._liked_ids)} liked tweets")

        # Seed the queue with all liked tweets
        for tid in self._liked_ids:
            self._enqueue(tid)

        # Process queue until empty
        while self._queue:
            tweet_id = self._queue.pop(0)
            await self._expand_tweet(tweet_id)

        logger.info(
            f"Expansion complete: {len(self._expanded_ids)} tweets total "
            f"({len(self._liked_ids)} liked, "
            f"{len(self._expanded_ids) - len(self._liked_ids)} discovered), "
            f"{self._api_calls} API calls"
        )

        # Convert to SimpleTweets with origin tracking
        return self._build_result()

    def _enqueue(self, tweet_id: str):
        """Add a tweet to the processing queue if not already expanded."""
        if tweet_id not in self._expanded_ids:
            self._expanded_ids.add(tweet_id)
            self._queue.append(tweet_id)

    async def _expand_tweet(self, tweet_id: str):
        """Expand a single tweet: fetch conversation, extract chain, handle quotes."""
        tweet = self._tweet_cache.get(tweet_id)
        if not tweet:
            # Need to fetch this tweet — it might be a quoted tweet or parent
            # we discovered but don't have data for yet
            await self._fetch_conversation_for_tweet(tweet_id)
            tweet = self._tweet_cache.get(tweet_id)
            if not tweet:
                logger.warning(f"Could not fetch tweet {tweet_id}, skipping")
                return

        # 1. Fetch the conversation this tweet belongs to
        conversation_id = tweet.get("conversation_id")
        if conversation_id:
            await self._fetch_conversation(conversation_id, tweet_id)
        else:
            # No conversation_id — fetch via TweetDetail using the tweet's own ID
            await self._fetch_conversation_for_tweet(tweet_id)

        # 2. Extract self-reply chain for this tweet's author
        self._expand_author_chain(tweet)

        # 3. Walk parent chain to root
        self._expand_parent_chain(tweet)

        # 4. Handle quoted tweet
        self._expand_quoted_tweet(tweet, discovered_via=tweet_id)

        # 5. Handle retweet
        self._expand_retweet(tweet, discovered_via=tweet_id)

        # 6. Check for liked replies to all tweets in the conversation
        self._check_liked_replies(tweet)

    async def _fetch_conversation(
        self, conversation_id: str, focal_tweet_id: str
    ):
        """Fetch a conversation and cache all its tweets."""
        if conversation_id in self._conversation_cache:
            return

        logger.debug(f"Fetching conversation {conversation_id} (focal: {focal_tweet_id})")
        try:
            self._api_calls += 1
            tweets = await self.tw_client.get_thread(
                focal_tweet_id, max_pages=5, page_delay=self.page_delay
            )

            self._conversation_cache[conversation_id] = tweets

            for tweet in tweets:
                tid = tweet.get("id")
                if tid and tid not in self._tweet_cache:
                    self._tweet_cache[tid] = tweet

            logger.debug(
                f"Conversation {conversation_id}: {len(tweets)} tweets cached"
            )
        except Exception as e:
            logger.error(f"Failed to fetch conversation {conversation_id}: {e}")
            raise

    async def _fetch_conversation_for_tweet(self, tweet_id: str):
        """Fetch conversation using a tweet ID when we don't know the conversation_id."""
        # Check if we already have this tweet cached
        if tweet_id in self._tweet_cache:
            tweet = self._tweet_cache[tweet_id]
            conv_id = tweet.get("conversation_id")
            if conv_id and conv_id in self._conversation_cache:
                return

        logger.debug(f"Fetching tweet detail for {tweet_id}")
        try:
            self._api_calls += 1
            tweets = await self.tw_client.get_thread(
                tweet_id, max_pages=3, page_delay=self.page_delay
            )

            # Determine conversation_id from the fetched tweets
            conv_id = None
            for tweet in tweets:
                if tweet.get("id") == tweet_id:
                    conv_id = tweet.get("conversation_id")
                    break
            if not conv_id and tweets:
                conv_id = tweets[0].get("conversation_id")

            if conv_id:
                # Merge with existing cache if any
                if conv_id in self._conversation_cache:
                    existing_ids = {t["id"] for t in self._conversation_cache[conv_id]}
                    for tweet in tweets:
                        if tweet["id"] not in existing_ids:
                            self._conversation_cache[conv_id].append(tweet)
                else:
                    self._conversation_cache[conv_id] = tweets

            for tweet in tweets:
                tid = tweet.get("id")
                if tid and tid not in self._tweet_cache:
                    self._tweet_cache[tid] = tweet
        except Exception as e:
            logger.error(f"Failed to fetch tweet detail for {tweet_id}: {e}")
            raise

    def _expand_author_chain(self, tweet: Dict[str, Any]):
        """
        Find all self-replies by the same author in the conversation,
        connected to this tweet. Both directions (up and down).
        """
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

        # Walk up from this tweet (following in_reply_to_status_id)
        current = tweet
        while current and current.get("author_username") == author:
            chain_ids.add(current["id"])
            parent_id = current.get("in_reply_to_status_id")
            if not parent_id:
                break
            parent = by_id.get(parent_id)
            if not parent or parent.get("author_username") != author:
                break
            current = parent

        # Walk down: find all self-replies connected to the chain
        changed = True
        while changed:
            changed = False
            for t in conv_tweets:
                if t.get("author_username") != author:
                    continue
                if t["id"] in chain_ids:
                    continue
                if t.get("in_reply_to_status_id") and t["in_reply_to_status_id"] in chain_ids:
                    chain_ids.add(t["id"])
                    changed = True

        # Enqueue all chain tweets
        for tid in chain_ids:
            if tid not in self._expanded_ids:
                t = self._tweet_cache.get(tid)
                if t:
                    self._enqueue(tid)

    def _expand_parent_chain(self, tweet: Dict[str, Any]):
        """Walk up the reply chain to the conversation root."""
        conversation_id = tweet.get("conversation_id")
        if not conversation_id:
            return

        conv_tweets = self._conversation_cache.get(conversation_id, [])
        by_id = {t["id"]: t for t in conv_tweets}

        current = tweet
        while current:
            parent_id = current.get("in_reply_to_status_id")
            if not parent_id:
                break

            # Enqueue the parent
            self._enqueue(parent_id)

            # If parent is in the conversation cache, also expand its author chain
            parent = by_id.get(parent_id)
            if parent:
                self._expand_author_chain(parent)
                current = parent
            else:
                # Parent not in this conversation — will be fetched when dequeued
                break

    def _expand_quoted_tweet(
        self, tweet: Dict[str, Any], discovered_via: str
    ):
        """If tweet quotes another, enqueue that quoted tweet for expansion."""
        # Check inline quoted_tweet object
        quoted = tweet.get("quoted_tweet")
        quoted_id = tweet.get("quoted_tweet_id")

        if quoted:
            qid = quoted.get("id")
            if qid:
                if qid not in self._tweet_cache:
                    self._tweet_cache[qid] = quoted
                self._enqueue(qid)

        elif quoted_id:
            # We have the ID but not the data — enqueue, will be fetched
            self._enqueue(quoted_id)

    def _expand_retweet(self, tweet: Dict[str, Any], discovered_via: str):
        """If tweet is a retweet, enqueue the original tweet for expansion."""
        if not tweet.get("is_retweet"):
            return

        retweeted_id = tweet.get("retweeted_tweet_id")
        if retweeted_id:
            self._enqueue(retweeted_id)

    def _check_liked_replies(self, tweet: Dict[str, Any]):
        """
        Check if any replies to this tweet (in the conversation) are in
        the user's liked set. If so, enqueue them.
        """
        conversation_id = tweet.get("conversation_id")
        if not conversation_id:
            return

        conv_tweets = self._conversation_cache.get(conversation_id, [])

        for t in conv_tweets:
            tid = t.get("id")
            if not tid:
                continue
            # Is this a reply to the current tweet AND is it liked?
            if t.get("in_reply_to_status_id") == tweet["id"] and tid in self._liked_ids:
                self._enqueue(tid)

        # Also check: any tweet in the expanded set that this tweet is a reply to
        # might have other liked replies we haven't checked yet.
        # This is handled naturally by the queue — when we process a tweet,
        # we check its conversation for liked replies.

    def _build_result(self) -> List[SimpleTweet]:
        """Convert all expanded tweets to SimpleTweets with proper origin tracking."""
        result = []

        for tweet_id in self._expanded_ids:
            tweet_data = self._tweet_cache.get(tweet_id)
            if not tweet_data:
                logger.warning(f"Tweet {tweet_id} in expanded set but not in cache")
                continue

            simple = SimpleTweet.from_api_dict(tweet_data)

            # Set origin
            if tweet_id in self._liked_ids:
                simple.origin = "liked"
            else:
                simple.origin = self._determine_origin(tweet_data)

            # Set discovered_via
            if tweet_id not in self._liked_ids:
                simple.discovered_via_tweet_id = self._find_discovered_via(tweet_data)

            # Set thread metadata
            self._set_thread_metadata(simple, tweet_data)

            result.append(simple)

        # Sort by created_at for consistent ordering
        result.sort(key=lambda t: t.created_at or datetime_min())

        return result

    def _determine_origin(self, tweet: Dict[str, Any]) -> str:
        """Determine origin reason for a non-liked tweet."""
        tid = tweet.get("id")

        # Check if it's a liked reply (liked AND is a reply to something in expanded set)
        if tid in self._liked_ids:
            return "liked"

        # Check if it's quoted by something in the expanded set
        for expanded_id in self._expanded_ids:
            cached = self._tweet_cache.get(expanded_id)
            if cached and cached.get("quoted_tweet_id") == tid:
                return "quoted"

        # Check if it's the original of a retweet
        for expanded_id in self._expanded_ids:
            cached = self._tweet_cache.get(expanded_id)
            if cached and cached.get("retweeted_tweet_id") == tid:
                return "retweet"

        # Check if it's a parent (something in expanded set replies to it)
        for expanded_id in self._expanded_ids:
            cached = self._tweet_cache.get(expanded_id)
            if cached and cached.get("in_reply_to_status_id") == tid:
                # Is it by the same author? Then it's a thread, otherwise parent
                child = cached
                if child.get("author_username") != tweet.get("author_username"):
                    return "parent"

        # Check conversation — if same author as a liked tweet, likely thread
        conversation_id = tweet.get("conversation_id")
        if conversation_id:
            for liked_id in self._liked_ids:
                liked = self._tweet_cache.get(liked_id)
                if (
                    liked
                    and liked.get("conversation_id") == conversation_id
                    and liked.get("author_username") == tweet.get("author_username")
                ):
                    return "thread"

        return "thread"  # Default: it's part of a thread expansion

    def _find_discovered_via(self, tweet: Dict[str, Any]) -> Optional[str]:
        """Find which liked tweet led to discovering this tweet."""
        conversation_id = tweet.get("conversation_id")
        if not conversation_id:
            return None

        # Find a liked tweet in the same conversation
        for liked_id in self._liked_ids:
            liked = self._tweet_cache.get(liked_id)
            if liked and liked.get("conversation_id") == conversation_id:
                return liked_id

        # Check if this was discovered via a quote
        for expanded_id in self._expanded_ids:
            cached = self._tweet_cache.get(expanded_id)
            if cached and cached.get("quoted_tweet_id") == tweet.get("id"):
                if expanded_id in self._liked_ids:
                    return expanded_id
                # Recurse up — find the liked tweet that led to the quoter
                return self._find_discovered_via(cached)

        return None

    def _set_thread_metadata(self, simple: SimpleTweet, tweet_data: Dict[str, Any]):
        """Set thread position metadata on a SimpleTweet."""
        conversation_id = tweet_data.get("conversation_id")
        if not conversation_id:
            simple.thread_position = "standalone"
            return

        conv_tweets = self._conversation_cache.get(conversation_id, [])
        author = tweet_data.get("author_username")

        # Check if author has self-replies in this conversation
        has_self_replies = any(
            t.get("in_reply_to_status_id") == tweet_data["id"]
            and t.get("author_username") == author
            for t in conv_tweets
        )

        is_root = not tweet_data.get("in_reply_to_status_id")

        if is_root and not has_self_replies:
            position = "standalone"
        elif is_root and has_self_replies:
            position = "root"
        elif not is_root and has_self_replies:
            position = "middle"
        else:
            position = "end"

        simple.thread_position = position
        simple.has_self_replies = has_self_replies
        simple.thread_root_id = conversation_id


def datetime_min():
    """Return a minimum datetime for sorting."""
    from datetime import datetime, timezone
    return datetime(1970, 1, 1, tzinfo=timezone.utc)
