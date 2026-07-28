"""Recursive tweet expander, v2 (phased, request-minimal).

Given a set of seed tweets (likes or bookmarks), archives everything connected:
self-reply threads (both directions), parent chains to the conversation root,
quoted tweets, URL-linked tweets, retweet originals — all recursively — plus
tombstones for tweets that existed in the graph but are gone.

Fetch strategy (see X_RESEARCH.md for the measurements behind it):
- Phase 1  REF CLOSURE: quoted/linked/retweeted/parent IDs are resolved via
  TweetResultsByRestIds, up to 100 per request, breadth-first until closure.
  Cost scales with graph depth, not tweet count. Missing IDs become tombstones.
- Phase 2  THREAD DISCOVERY: for tweets with reply_count > 0, the author's tweets
  in that conversation are fetched via SearchTimeline, one `(from:author
  conversation_id:conv)` per query, sliced with `max_id:` past the result cap.
  reply_count == 0 proves standalone: zero requests.
- Fallback: TweetDetail (relevance-stop pagination) when the author is protected
  (invisible to search) or the search index provably missed the conversation.

The phases loop until nothing new is discovered, then one pass adopts what was
already fetched but never chained onto anything: the author's replies made under
other people, and the bystanders a fallback returned along with the tree. Neither
costs a search request, and both land after the loop so they cannot generate more.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from social_archiver.platforms.twitter.client import TwitterClient
from social_archiver.platforms.twitter.simple_tweet import SimpleTweet

logger = logging.getLogger(__name__)

# Expansion walks the reply and quote graph breadth-first, so the rounds it needs are the graph's
# depth rather than a budget. Every round strictly consumes from finite pools — pairs not yet
# searched, ids not yet resolved — so the loop ends on its own; this only stops a runaway. Ten was
# low enough to cut off runs that were still discovering, and discovery lost that way never comes
# back: pairs are derived from the run's own seeds, so a later run never revisits them.
MAX_ITERATIONS = 50

# The API returns a bare null for a tweet it will not serve, whether it was deleted or is
# withheld from this connection's country. Protected and suspended arrive with a stated reason;
# these do not, so the text says what is known rather than picking a cause.
TOMBSTONE_REASON_BATCH = "unavailable, no reason given (deleted, or withheld from this connection)"


def datetime_min() -> datetime:
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _is_rate_limit(e: Exception) -> bool:
    msg = str(e).lower()
    return "429" in msg or "rate" in msg


def _refs_of(tweet: dict[str, Any]) -> list[str]:
    refs = []
    if quoted_id := tweet.get("quoted_tweet_id"):
        refs.append(quoted_id)
    if retweeted_id := tweet.get("retweeted_tweet_id"):
        refs.append(retweeted_id)
    if parent_id := tweet.get("in_reply_to_status_id"):
        refs.append(parent_id)
    refs.extend(tweet.get("linked_tweet_ids") or [])
    return refs


class TweetExpander:
    def __init__(
        self,
        tw_client: TwitterClient,
        page_delay: float = 1.5,
        seed_origin: str = "liked",
        searched: set[tuple[str, str]] | None = None,
    ):
        self.tw_client = tw_client
        self.page_delay = page_delay
        self.seed_origin = seed_origin

        self._tweets: dict[str, dict[str, Any]] = {}  # all known tweets (full data preferred over shallow stubs)
        self._archived: set[str] = set()  # ids selected for the archive
        self._tombstones: dict[str, str] = {}
        self._seed_ids: set[str] = set()
        self._probe_ids: set[str] = set()  # fetch for graph knowledge only, don't archive
        self._unresolvable: set[str] = set()  # requested and not returned; don't re-request
        self._searched_pairs: set[tuple[str, str]] = set(searched or ())
        self._newly_searched: set[tuple[str, str]] = set()  # this run's, for the caller to persist
        self._detail_fetched: set[str] = set()  # conversations fetched via TweetDetail fallback
        self._thread_found: set[str] = set()  # ids search returned, archived or not
        self._loose_replies: set[str] = set()  # author's replies under others in the conversation
        self._fallback_seen: set[str] = set()  # every id a TweetDetail fallback returned
        self._bystanders: set[str] = set()  # fallback tweets by authors the chain walk skipped

    @property
    def newly_searched(self) -> set[tuple[str, str]]:
        """Pairs this run actually searched. Pairs it was told to skip are excluded, so their
        recorded time keeps ageing and the conversation is eventually walked again."""
        return self._newly_searched

    async def expand(self, seed_tweets: list[dict[str, Any]]) -> list[SimpleTweet]:
        """Entry point: expands raw seed tweet dicts (from get_all_likes /
        get_all_bookmarks) into SimpleTweets with origin and linking fields set."""
        requests_before = self.tw_client.request_count

        for tweet in seed_tweets:
            if tid := tweet.get("id"):
                self._seed_ids.add(tid)
                self._ingest(tweet)
                self._archived.add(tid)

        logger.info(f"Starting expansion of {len(self._seed_ids)} seed tweets (origin={self.seed_origin})")

        for _ in range(MAX_ITERATIONS):
            progress = await self._resolve_refs()
            progress |= await self._discover_threads()
            if not progress:
                break
        else:
            self._log_unfinished()

        await self._adopt_unchained()

        tombstoned = sum(1 for tid in self._archived if tid not in self._tweets)
        logger.info(
            f"Expansion complete: {len(self._archived)} tweets total "
            f"({len(self._seed_ids)} seeds, {len(self._archived) - len(self._seed_ids)} discovered, "
            f"{tombstoned} tombstoned, {len(self._loose_replies)} loose replies, "
            f"{len(self._bystanders)} bystanders), "
            f"{self.tw_client.request_count - requests_before} HTTP requests"
        )

        return self._build_result()

    async def _adopt_unchained(self):
        """Keep everything already fetched that the chain walk would otherwise discard.

        Two populations, neither of which costs a search request. The author's replies made under
        other people came back from the conversation search that was run anyway. The rest of a
        conversation comes back from the TweetDetail fallback, which returns the whole tree and
        was until now read only for the liked author's own chain.

        This runs after the rounds converge, for two reasons. Chaining has settled, so a
        self-reply whose parent resolved late is not filed as a loose one. And nothing archived
        here can reach `_pending_pairs`, which would turn every bystander's author into another
        conversation search and spend the one bucket that actually constrains the run.

        Their parents are then resolved in their own right, on the batch endpoint: a reply is
        unreadable without the tweet it answers, and that tweet is by a different author, so
        nothing else in the expansion would ever reach it."""
        self._loose_replies = self._thread_found - self._archived
        self._bystanders = self._fallback_seen - self._archived - self._loose_replies
        adopted = self._loose_replies | self._bystanders
        if not adopted:
            return
        self._archived |= adopted
        for _ in range(MAX_ITERATIONS):
            for tid in adopted:
                self._adopt_ancestors(tid)
            if not await self._resolve_refs():
                break

    def _adopt_ancestors(self, tid: str):
        """Archive the ancestors of a reply that are already known.

        A parent pulled in as a probe sits in `_tweets` without being archived, and
        `_pending_ref_ids` only collects ids it has yet to fetch, so it passes over exactly these.
        Stops at the first unknown ancestor; that one is a reference of an archived tweet now, so
        the resolve pass fetches it and the next round walks further up."""
        seen: set[str] = set()
        while (parent := self._tweets.get(tid, {}).get("in_reply_to_status_id")) and parent not in seen:
            seen.add(parent)
            if parent not in self._tweets:
                return
            self._archived.add(parent)
            tid = parent

    def _log_unfinished(self):
        """Say what the round cap left behind. Stopping here is not a partial result that finishes
        later: unsearched pairs are derived from this run's own working set, so once it returns
        they are gone from view."""
        pairs, fallback = self._pending_pairs()
        refs, probes = self._pending_ref_ids()
        logger.warning(
            f"Expansion hit MAX_ITERATIONS={MAX_ITERATIONS} while still finding work: "
            f"{len(pairs)} conversations unsearched, {len(fallback)} awaiting the TweetDetail "
            f"fallback, {len(refs | probes)} references unresolved. Nothing revisits them."
        )

    def _ingest(self, tweet: dict[str, Any]):
        """Add a tweet to the knowledge set; full data wins over shallow stubs.
        Embedded quoted tweets are ingested as stubs so their content survives
        even if the tweet is deleted before the batch lookup resolves it."""
        tid = tweet.get("id")
        if not tid:
            return
        existing = self._tweets.get(tid)
        if existing is None or (existing.get("shallow") and not tweet.get("shallow")):
            self._tweets[tid] = tweet
        if (quoted := tweet.get("quoted_tweet")) and quoted.get("id"):
            self._ingest(quoted)

    # =========================================================================
    # Phase 1: reference closure via batch lookup
    # =========================================================================

    def _pending_ref_ids(self) -> tuple[set[str], set[str]]:
        """(refs to archive, probe-only ids) that still need resolution."""
        done = {tid for tid, t in self._tweets.items() if not t.get("shallow")}
        done |= set(self._tombstones) | self._unresolvable

        refs: set[str] = set()
        for tid in self._archived:
            tweet = self._tweets.get(tid)
            if not tweet:
                continue
            if tweet.get("shallow") and tid not in self._unresolvable:
                refs.add(tid)  # re-resolve the stub itself: its own refs are unknown
            refs.update(r for r in _refs_of(tweet) if r not in done)

        probes = {p for p in self._probe_ids if p not in done and p not in refs}
        return refs, probes

    async def _resolve_refs(self) -> bool:
        refs, probes = self._pending_ref_ids()
        to_fetch = list(refs | probes)
        if not to_fetch:
            return False

        logger.info(f"Batch-resolving {len(to_fetch)} referenced tweets ({len(refs)} to archive, {len(probes)} probes)")
        result = await self.tw_client.get_tweets_by_ids(to_fetch, page_delay=self.page_delay)

        for tweet in result.tweets:
            self._ingest(tweet)

        for tid in result.missing:
            self._unresolvable.add(tid)
            if self._tweets.get(tid, {}).get("shallow"):
                # the embedded stub is all that's left of it — archive the stub, not a tombstone
                self._tweets[tid].pop("shallow", None)
                logger.info(f"Tweet {tid} vanished after being embedded; keeping quote-card stub")
            elif tid in refs:
                reason = result.unavailable.get(tid, TOMBSTONE_REASON_BATCH)
                self._tombstones.setdefault(tid, reason)
                logger.info(f"Tweet {tid} unavailable ({reason}), recording tombstone")

        self._archived.update(refs)
        return True

    # =========================================================================
    # Phase 2: thread discovery via OR-batched conversation search
    # =========================================================================

    def _pending_pairs(self) -> tuple[list[tuple[str, str]], dict[str, str]]:
        """(author, conversation) pairs to search; protected-author conversations
        go straight to the TweetDetail fallback (search can't see them). Liking several
        tweets in one thread yields one pair per tweet, and they all produce the same
        query term, so a repeat only costs room in the batched request."""
        pairs: set[tuple[str, str]] = set()
        fallback: dict[str, str] = {}  # conversation_id -> focal tweet id

        for tid in self._archived:
            tweet = self._tweets.get(tid)
            if not tweet or not (tweet.get("reply_count") or 0):
                continue
            author = tweet.get("author_username")
            conv = tweet.get("conversation_id") or tid
            if not author:
                continue
            if tweet.get("author_protected"):
                if conv not in self._detail_fetched:
                    fallback[conv] = tid
                continue
            pair = (author, conv)
            if pair not in self._searched_pairs:
                pairs.add(pair)

        return sorted(pairs), fallback

    async def _discover_threads(self) -> bool:
        pairs, fallback_convs = self._pending_pairs()

        found: list[dict[str, Any]] = []
        if pairs:
            logger.info(f"Searching {len(pairs)} (author, conversation) pairs for thread tweets")
            found = await self.tw_client.get_conversation_author_tweets(pairs, page_delay=self.page_delay)
            self._searched_pairs.update(pairs)
            self._newly_searched.update(pairs)
            for tweet in found:
                self._ingest(tweet)
                self._thread_found.add(tweet["id"])

        # search index gap detection: each searched pair must at least return the
        # tweet that generated it — if not, fetch that conversation via TweetDetail
        found_pairs = {(t.get("author_username"), t.get("conversation_id")) for t in found}
        for author, conv in pairs:
            if (author, conv) not in found_pairs and conv not in self._detail_fetched:
                focal = next(
                    (
                        tid
                        for tid in self._archived
                        if (t := self._tweets.get(tid))
                        and t.get("author_username") == author
                        and (t.get("conversation_id") or tid) == conv
                    ),
                    None,
                )
                if focal:
                    logger.info(f"Search returned nothing for (@{author}, {conv}); falling back to TweetDetail")
                    fallback_convs.setdefault(conv, focal)

        for conv, focal in fallback_convs.items():
            await self._fetch_conversation_fallback(conv, focal)

        # always recompute chains: refs resolved in Phase 1 (parents, tombstoned
        # bridges) can connect tweets found by earlier searches
        archived_before = len(self._archived)
        probes_before = len(self._probe_ids)
        self._archive_thread_chains()

        return (
            bool(pairs or fallback_convs)
            or len(self._archived) > archived_before
            or len(self._probe_ids) > probes_before
        )

    async def _fetch_conversation_fallback(self, conversation_id: str, focal_tweet_id: str):
        self._detail_fetched.add(conversation_id)
        try:
            result = await self.tw_client.get_thread(focal_tweet_id, page_delay=self.page_delay)
        except Exception as e:
            if _is_rate_limit(e):
                raise
            logger.error(f"TweetDetail fallback failed for conversation {conversation_id}: {e}")
            return
        for tweet in result.tweets:
            self._ingest(tweet)
            if tid := tweet.get("id"):
                self._fallback_seen.add(tid)
        self._tombstones.update(result.tombstones)

    def _archive_thread_chains(self):
        """Archive author self-reply chains connected to already-archived tweets.
        Recomputed over all known tweets each round — new knowledge (resolved
        parents, tombstoned bridges) can connect previously dangling tweets."""
        by_conv: dict[str, list[dict[str, Any]]] = {}
        for tweet in self._tweets.values():
            if conv := tweet.get("conversation_id"):
                by_conv.setdefault(conv, []).append(tweet)

        for conv, tweets in by_conv.items():
            archived_here = [t for t in tweets if t["id"] in self._archived]
            if not archived_here:
                continue
            for author in {t.get("author_username") for t in archived_here}:
                self._archive_author_chain(author, tweets)

    def _archive_author_chain(self, author: str, conv_tweets: list[dict[str, Any]]):
        candidates = [t for t in conv_tweets if t.get("author_username") == author]

        chain: set[str] = {t["id"] for t in candidates if t["id"] in self._archived}
        changed = True
        while changed:
            changed = False
            for t in candidates:
                if t["id"] in chain:
                    continue
                parent_id = t.get("in_reply_to_status_id")
                # connected through the chain, or the parent is gone entirely
                # (deleted bridge — benefit of the doubt keeps the content)
                parent_gone = parent_id in self._tombstones or (
                    parent_id in self._unresolvable and parent_id not in self._tweets
                )
                if parent_id in chain or parent_gone:
                    chain.add(t["id"])
                    changed = True
                elif parent_id and parent_id not in self._tweets:
                    # unknown parent: probe it so the next round can decide
                    self._probe_ids.add(parent_id)

        for tid in chain:
            if tid not in self._archived:
                self._archived.add(tid)
                logger.debug(f"Thread tweet archived: {tid} by @{author}")

    # =========================================================================
    # Result assembly
    # =========================================================================

    def _build_result(self) -> list[SimpleTweet]:
        result = []

        for tweet_id in self._archived:
            tweet_data = self._tweets.get(tweet_id)
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
            text=self._tombstones.get(tweet_id, TOMBSTONE_REASON_BATCH),
            author_username="unknown",
            author_name="unknown",
            is_tombstone=True,
        )
        simple.origin = self.seed_origin if tweet_id in self._seed_ids else self._determine_origin(stub)
        simple.discovered_via_tweet_id = self._find_discovered_via(stub)
        return simple

    def _determine_origin(self, tweet: dict[str, Any]) -> str:
        tid = tweet.get("id")

        for archived_id in self._archived:
            cached = self._tweets.get(archived_id)
            if cached and cached.get("quoted_tweet_id") == tid:
                return "quoted"

        for archived_id in self._archived:
            cached = self._tweets.get(archived_id)
            if cached and cached.get("retweeted_tweet_id") == tid:
                return "retweet"

        for archived_id in self._archived:
            cached = self._tweets.get(archived_id)
            if cached and tid in (cached.get("linked_tweet_ids") or []):
                return "linked"

        for archived_id in self._archived:
            cached = self._tweets.get(archived_id)
            if cached and cached.get("in_reply_to_status_id") == tid:
                if cached.get("author_username") != tweet.get("author_username"):
                    return "parent"

        if tid in self._loose_replies:
            return "reply"
        if tid in self._bystanders:
            return "conversation"

        return "thread"

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
                seed = self._tweets.get(seed_id)
                if seed and seed.get("conversation_id") == conversation_id:
                    return seed_id

        for archived_id in self._archived:
            cached = self._tweets.get(archived_id)
            if not cached:
                continue
            if cached.get("quoted_tweet_id") == tid or tid in (cached.get("linked_tweet_ids") or []):
                if archived_id in self._seed_ids:
                    return archived_id
                return self._find_discovered_via(cached, seen)

        return None

    def _set_thread_metadata(self, simple: SimpleTweet, tweet_data: dict[str, Any]):
        conversation_id = tweet_data.get("conversation_id")
        if not conversation_id:
            simple.thread_position = "standalone"
            return

        author = tweet_data.get("author_username")
        has_self_replies = any(
            t.get("in_reply_to_status_id") == tweet_data["id"] and t.get("author_username") == author
            for t in self._tweets.values()
            if t.get("conversation_id") == conversation_id
        )
        is_root = not tweet_data.get("in_reply_to_status_id")

        if is_root:
            position = "root" if has_self_replies else "standalone"
        else:
            position = "middle" if has_self_replies else "end"

        simple.thread_position = position
        simple.has_self_replies = has_self_replies
        simple.thread_root_id = conversation_id
