"""Twitter GraphQL API client, ported from the bird TypeScript library.
Uses cookie-based authentication (auth_token + ct0) to access Twitter's
internal GraphQL endpoints.
"""
import asyncio
import json
import logging
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from social_archiver.platforms.twitter import config, content

logger = logging.getLogger(__name__)

# Query IDs for the LOGGED-IN x.com app (the one these auth cookies target). The
# logged-out site is a separate TanStack rewrite with its own, incompatible query
# IDs — do NOT harvest these from the public logged-out bundle; its IDs 422 against
# the authenticated backend. These rotate — reharvest from the logged-in app on 404s.
QUERY_IDS = {
    "Bookmarks": "RV1g3b8n_SGOHwkqKYSCFw",
    "BookmarkFolderTimeline": "KJIQpsvxrTfRIlbaRIySHQ",
    "Likes": "JR2gceKucIKcVNB_9JkhsA",
    "UserTweets": "hr4gzZONlq23okjU8fIe_A",
    "TweetDetail": "97JF30KziU00483E_8elBA",
    "SearchTimeline": "Bcw3RzK-PatNAmbnw54hFw",
    "TweetResultsByRestIds": "7nfIZg-03g-BuVG0Oa1fXA",
    "TweetResultByRestId": "-4_LMahNlI4MuLJ-EAFEog",
    "HomeTimeline": "edseUwk9sP5Phz__9TIRnA",
    "HomeLatestTimeline": "iOEZpOdfekFsxSlPQCQtPg",
}

# Fallback query IDs to try when primary ones get 404
FALLBACK_QUERY_IDS = {
    "Bookmarks": ["tmd4ifV8RHltzn8ymGg1aw"],
    "Likes": ["tl9f_I0xyREhFd5KMzuO7w", "ETJflBunfqNa1uE1mBPCaw"],
    "UserTweets": ["Wms1GvIiHXAPBaCr9KblaA"],
    "TweetDetail": ["jd3V43oDY9cY7obs1YMfbQ", "_NvJCnIjOW__EP5-RF197A"],
    "SearchTimeline": ["M1jEez78PEfVfbQLvlWMvQ"],
}

# Feature flags required by Twitter's GraphQL API
TIMELINE_FEATURES = {
    "rweb_video_screen_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": False,
    "responsive_web_grok_annotations_enabled": False,
    "responsive_web_jetfuel_frame": True,
    "post_ctas_fetch_enabled": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "responsive_web_grok_show_grok_translated_post": False,
    "responsive_web_grok_analysis_button_from_backend": True,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": False,
    "responsive_web_enhance_cards_enabled": False,
    "blue_business_profile_image_shape_enabled": True,
    "responsive_web_text_conversations_enabled": False,
    "tweetypie_unmention_optimization_enabled": True,
    "vibe_api_enabled": True,
    "responsive_web_twitter_blue_verified_badge_is_enabled": True,
    "interactive_text_enabled": True,
    "longform_notetweets_richtext_consumption_enabled": True,
    "responsive_web_media_download_video_enabled": False,
}

# So an Article's full body comes inline on the tweet result, with no extra per-article fetch.
ARTICLE_FEATURES = {
    "articles_rest_api_enabled": True,
    "responsive_web_twitter_article_plain_text_enabled": True,
}

ARTICLE_FIELD_TOGGLES = {
    "withArticleRichContentState": True,
    "withArticlePlainText": True,
}

BOOKMARKS_FEATURES = {**TIMELINE_FEATURES, **ARTICLE_FEATURES, "graphql_timeline_v2_bookmark_timeline": True}

LIKES_FEATURES = {**TIMELINE_FEATURES, **ARTICLE_FEATURES}

TWEET_DETAIL_FEATURES = {
    **TIMELINE_FEATURES,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "communities_web_enable_tweet_community_results_fetch": True,
    "responsive_web_twitter_article_plain_text_enabled": True,
    "responsive_web_twitter_article_seed_tweet_detail_enabled": True,
    "responsive_web_twitter_article_seed_tweet_summary_enabled": True,
    "articles_preview_enabled": True,
    "articles_rest_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True,
}

TWEET_DETAIL_FIELD_TOGGLES = {
    "withPayments": False,
    "withAuxiliaryUserLabels": False,
    "withArticleRichContentState": True,
    "withArticlePlainText": True,
    "withGrokAnalyze": False,
    "withDisallowedReplyControls": False,
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass
class ThreadResult:
    """Tweets from a conversation plus tombstones for tweets that exist in the
    reply graph but are no longer available (deleted, protected, suspended)."""
    tweets: list[dict[str, Any]] = field(default_factory=list)
    tombstones: dict[str, str] = field(default_factory=dict)  # tweet_id -> reason


@dataclass
class BatchLookupResult:
    """Result of a TweetResultsByRestIds batch lookup."""
    tweets: list[dict[str, Any]] = field(default_factory=list)
    missing: set[str] = field(default_factory=set)  # requested but not returned (deleted/protected/suspended)


class TwitterClient:
    """Python port of bird's TwitterClient using cookie-based auth."""

    def __init__(self, auth_token: str | None = None, ct0: str | None = None):
        self.auth_token = auth_token or config.TWITTER_AUTH_TOKEN
        self.ct0 = ct0 or config.TWITTER_CT0
        self.client_uuid = str(uuid.uuid4())
        self.client_device_id = str(uuid.uuid4())
        self.client_user_id: str | None = None
        self.request_count = 0  # every HTTP request to the API, for honest cost logging
        self._http_client: httpx.AsyncClient | None = None

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "authorization": f"Bearer {config.TWITTER_BEARER_TOKEN}",
            "x-csrf-token": self.ct0,
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "x-client-uuid": self.client_uuid,
            "x-twitter-client-deviceid": self.client_device_id,
            "x-client-transaction-id": secrets.token_hex(16),
            "cookie": f"auth_token={self.auth_token}; ct0={self.ct0}",
            "user-agent": USER_AGENT,
            "content-type": "application/json",
            "origin": "https://x.com",
            "referer": "https://x.com/",
        }
        if self.client_user_id:
            headers["x-twitter-client-user-id"] = self.client_user_id
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        return self._http_client

    async def _wait_for_rate_limit(self, response: httpx.Response) -> bool:
        """Sleep until the bucket resets (per x-rate-limit-reset). Returns True if
        a retry makes sense. Waits are capped at 16 minutes."""
        reset = response.headers.get("x-rate-limit-reset")
        try:
            wait = min(max(int(reset) - time.time() + 5, 10), 960) if reset else 60
        except ValueError:
            wait = 60
        logger.warning(f"Rate limited (429); sleeping {wait:.0f}s until bucket reset")
        await asyncio.sleep(wait)
        return True

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def _graphql_get(
        self,
        operation: str,
        query_id: str,
        variables: dict[str, Any],
        features: dict[str, bool],
        field_toggles: dict[str, bool] | None = None,
    ) -> tuple[bool, dict | None, str | None]:
        """Make a GraphQL GET request to Twitter's API."""
        params = {
            "variables": json.dumps(variables, separators=(",", ":")),
            "features": json.dumps(features, separators=(",", ":")),
        }
        if field_toggles:
            params["fieldToggles"] = json.dumps(field_toggles, separators=(",", ":"))

        url = f"{config.TWITTER_API_BASE}/{query_id}/{operation}?{urlencode(params)}"

        client = await self._get_client()
        try:
            self.request_count += 1
            response = await client.get(url, headers=self._get_headers())

            if response.status_code == 429 and await self._wait_for_rate_limit(response):
                self.request_count += 1
                response = await client.get(url, headers=self._get_headers())

            if response.status_code == 404:
                return False, None, f"HTTP 404 (query_id={query_id})"
            if response.status_code == 429:
                return False, None, "Rate limited (429)"
            if response.status_code != 200:
                return False, None, f"HTTP {response.status_code}: {response.text[:200]}"

            data = response.json()

            if data.get("errors"):
                error_msg = ", ".join(e.get("message", "") for e in data["errors"])
                if data.get("data"):
                    logger.warning(f"GraphQL errors (non-fatal): {error_msg}")
                else:
                    return False, None, error_msg

            return True, data, None

        except httpx.TimeoutException:
            return False, None, "Request timed out"
        except Exception as e:
            return False, None, str(e)

    async def _graphql_get_with_fallbacks(
        self,
        operation: str,
        query_ids: list[str],
        variables: dict[str, Any],
        features: dict[str, bool],
        field_toggles: dict[str, bool] | None = None,
    ) -> tuple[bool, dict | None, str | None]:
        """Try multiple query IDs for a GraphQL operation."""
        last_error = None
        for query_id in query_ids:
            success, data, error = await self._graphql_get(operation, query_id, variables, features, field_toggles)
            if success:
                return True, data, None
            last_error = error
            if error and "404" not in error:
                return False, None, error
        return False, None, last_error or "All query IDs failed"

    def _get_query_ids(self, operation: str) -> list[str]:
        primary = QUERY_IDS.get(operation, "")
        fallbacks = FALLBACK_QUERY_IDS.get(operation, [])
        ids = [primary] + fallbacks if primary else fallbacks
        return [qid for qid in ids if qid]

    # =========================================================================
    # TweetDetail (conversation/thread fetching)
    # =========================================================================

    async def get_tweet_detail(self, tweet_id: str, cursor: str | None = None) -> dict[str, Any]:
        """Fetch the focal tweet + its full conversation. Key endpoint for thread expansion."""
        variables = {
            "focalTweetId": tweet_id,
            "with_rux_injections": False,
            "rankingMode": "Relevance",
            "includePromotedContent": True,
            "withCommunity": True,
            "withQuickPromoteEligibilityTweetFields": True,
            "withBirdwatchNotes": True,
            "withVoice": True,
        }
        if cursor:
            variables["cursor"] = cursor

        query_ids = self._get_query_ids("TweetDetail")
        success, data, error = await self._graphql_get_with_fallbacks(
            "TweetDetail", query_ids, variables, TWEET_DETAIL_FEATURES, field_toggles=TWEET_DETAIL_FIELD_TOGGLES
        )

        if not success:
            if error and "404" in error:
                for query_id in query_ids:
                    post_success, post_data, _ = await self._graphql_post(
                        "TweetDetail", query_id, variables, TWEET_DETAIL_FEATURES, field_toggles=TWEET_DETAIL_FIELD_TOGGLES
                    )
                    if post_success:
                        return self._parse_tweet_detail_response(post_data, tweet_id)
            raise RuntimeError(f"TweetDetail failed for {tweet_id}: {error}")

        return self._parse_tweet_detail_response(data, tweet_id)

    async def _graphql_post(
        self,
        operation: str,
        query_id: str,
        variables: dict[str, Any],
        features: dict[str, bool],
        field_toggles: dict[str, bool] | None = None,
    ) -> tuple[bool, dict | None, str | None]:
        """GraphQL POST request (fallback for 404 on GET)."""
        url = f"{config.TWITTER_API_BASE}/{query_id}/{operation}"
        body = {"variables": variables, "features": features, "queryId": query_id}
        if field_toggles:
            body["fieldToggles"] = field_toggles

        client = await self._get_client()
        try:
            self.request_count += 1
            response = await client.post(url, headers=self._get_headers(), json=body)
            if response.status_code == 404:
                return False, None, f"HTTP 404 POST (query_id={query_id})"
            if response.status_code == 429:
                return False, None, "Rate limited (429)"
            if response.status_code != 200:
                return False, None, f"HTTP {response.status_code}: {response.text[:200]}"
            data = response.json()
            if data.get("errors"):
                error_msg = ", ".join(e.get("message", "") for e in data["errors"])
                if data.get("data"):
                    logger.warning(f"GraphQL POST errors (non-fatal): {error_msg}")
                else:
                    return False, None, error_msg
            return True, data, None
        except httpx.TimeoutException:
            return False, None, "Request timed out"
        except Exception as e:
            return False, None, str(e)

    def _parse_tweet_detail_response(self, data: dict, focal_tweet_id: str) -> dict[str, Any]:
        data_root = data.get("data", {})
        focal_result = data_root.get("tweetResult", {}).get("result")

        instructions = data_root.get("threaded_conversation_with_injections_v2", {}).get("instructions", [])
        all_tweets = _parse_tweets_from_instructions(instructions, exclude_related=True)

        if focal_result:
            focal_mapped = _map_tweet_result(focal_result)
            if focal_mapped:
                existing_ids = {t["id"] for t in all_tweets}
                if focal_mapped["id"] not in existing_ids:
                    all_tweets.insert(0, focal_mapped)

        if not focal_result:
            focal_result = _find_tweet_in_instructions(instructions, focal_tweet_id)

        return {
            "success": True,
            "tweets": all_tweets,
            "tombstones": _parse_tombstones_from_instructions(instructions),
            "next_cursor": _extract_cursor(instructions),
        }

    async def get_thread(self, tweet_id: str, page_delay: float = 1.0, relevance_stop: bool = True) -> ThreadResult:
        """Fetch the conversation thread for a tweet. With relevance_stop (default),
        pagination continues only while pages still add tweets by authors in the focal
        tweet's ancestor/self-thread chain — a viral tweet's endless stranger-reply
        pages are not fetched. relevance_stop=False paginates to cursor exhaustion."""
        result = ThreadResult()
        seen_ids = set()
        cursor = None
        pages = 0
        relevant_authors: set[str] | None = None

        while True:
            if pages > 0 and page_delay > 0:
                await asyncio.sleep(page_delay)

            page = await self.get_tweet_detail(tweet_id, cursor=cursor)
            pages += 1

            added = 0
            added_relevant = 0
            for tweet in page.get("tweets", []):
                tid = tweet.get("id")
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    result.tweets.append(tweet)
                    added += 1
                    if relevant_authors and tweet.get("author_username") in relevant_authors:
                        added_relevant += 1

            result.tombstones.update(page.get("tombstones", {}))

            if relevant_authors is None:
                relevant_authors = _chain_authors(result.tweets, tweet_id)

            next_cursor = page.get("next_cursor")
            if not next_cursor or next_cursor == cursor or added == 0:
                break
            if relevance_stop and pages > 1 and added_relevant == 0:
                break
            cursor = next_cursor

        if pages > 5:
            logger.info(f"Thread {tweet_id}: {len(result.tweets)} tweets over {pages} pages")
        return result

    # =========================================================================
    # Batch tweet lookup (TweetResultsByRestIds)
    # =========================================================================

    async def get_tweets_by_ids(
        self, tweet_ids: list[str], chunk_size: int = 100, page_delay: float = 1.5
    ) -> BatchLookupResult:
        """Resolve up to chunk_size tweets per request. Missing IDs (deleted,
        protected, suspended) are reported in `missing` — no reason text available."""
        result = BatchLookupResult()
        requested = list(dict.fromkeys(tweet_ids))

        for i in range(0, len(requested), chunk_size):
            if i > 0 and page_delay > 0:
                await asyncio.sleep(page_delay)
            chunk = requested[i : i + chunk_size]

            variables = {
                "tweetIds": chunk,
                "includePromotedContent": False,
                "withBirdwatchNotes": True,
                "withVoice": True,
                "withCommunity": True,
            }
            query_ids = self._get_query_ids("TweetResultsByRestIds")
            success, data, error = await self._graphql_get_with_fallbacks(
                "TweetResultsByRestIds", query_ids, variables, TWEET_DETAIL_FEATURES, field_toggles=ARTICLE_FIELD_TOGGLES
            )
            if not success:
                raise RuntimeError(f"TweetResultsByRestIds failed for {len(chunk)} ids: {error}")

            returned_ids = set()
            for item in data.get("data", {}).get("tweetResult") or []:
                mapped = _map_tweet_result(item.get("result")) if isinstance(item, dict) else None
                if mapped:
                    returned_ids.add(mapped["id"])
                    result.tweets.append(mapped)
            result.missing.update(tid for tid in chunk if tid not in returned_ids)

        return result

    # =========================================================================
    # Search (SearchTimeline) — targeted conversation/thread fetching
    # =========================================================================

    async def search_tweets(
        self, query: str, product: str = "Latest", max_pages: int = 20, page_delay: float = 1.5
    ) -> list[dict[str, Any]]:
        """Run a search query, paginating the Bottom cursor to exhaustion.
        Page size is capped at 20 by the API regardless of count."""
        all_tweets: list[dict[str, Any]] = []
        seen: set[str] = set()
        cursor = None

        for page in range(max_pages):
            if page > 0 and page_delay > 0:
                await asyncio.sleep(page_delay)

            variables = {"rawQuery": query, "count": 20, "querySource": "typed_query", "product": product}
            if cursor:
                variables["cursor"] = cursor

            success, data, error = await self._search_page(variables)
            if not success:
                raise RuntimeError(f"SearchTimeline failed for {query!r}: {error}")

            timeline = data.get("data", {}).get("search_by_raw_query", {}).get("search_timeline", {})
            instructions = timeline.get("timeline", {}).get("instructions", [])

            added = 0
            for tweet in _parse_tweets_from_instructions(instructions):
                if tweet["id"] not in seen:
                    seen.add(tweet["id"])
                    all_tweets.append(tweet)
                    added += 1

            next_cursor = _extract_cursor(instructions)
            if not next_cursor or next_cursor == cursor or added == 0:
                break
            cursor = next_cursor

        return all_tweets

    async def _search_page(self, variables: dict[str, Any]) -> tuple[bool, dict | None, str | None]:
        """SearchTimeline uses POST with variables in the URL and features in the
        body (matches the web client; GET returns errors for this operation)."""
        client = await self._get_client()
        last_error = None
        for query_id in self._get_query_ids("SearchTimeline"):
            params = {"variables": json.dumps(variables, separators=(",", ":"))}
            url = f"{config.TWITTER_API_BASE}/{query_id}/SearchTimeline?{urlencode(params)}"
            try:
                self.request_count += 1
                response = await client.post(
                    url, headers=self._get_headers(), json={"features": TIMELINE_FEATURES, "queryId": query_id}
                )
                if response.status_code == 429 and await self._wait_for_rate_limit(response):
                    self.request_count += 1
                    response = await client.post(
                        url, headers=self._get_headers(), json={"features": TIMELINE_FEATURES, "queryId": query_id}
                    )
                if response.status_code == 404:
                    last_error = f"HTTP 404 (query_id={query_id})"
                    continue
                if response.status_code == 429:
                    return False, None, "Rate limited (429)"
                if response.status_code != 200:
                    return False, None, f"HTTP {response.status_code}: {response.text[:200]}"
                data = response.json()
                if data.get("errors") and not data.get("data"):
                    return False, None, ", ".join(e.get("message", "") for e in data["errors"])
                return True, data, None
            except httpx.TimeoutException:
                return False, None, "Request timed out"
        return False, None, last_error or "All query IDs failed"

    async def get_conversation_author_tweets(
        self, pairs: list[tuple[str, str]], batch_size: int = 5, page_delay: float = 1.5
    ) -> list[dict[str, Any]]:
        """Fetch all tweets each author posted in their conversation, OR-batching
        multiple (author_username, conversation_id) pairs into one search query.
        Returns only what the search index can see (misses protected authors)."""
        all_tweets: list[dict[str, Any]] = []
        unique_pairs = list(dict.fromkeys(pairs))

        for i in range(0, len(unique_pairs), batch_size):
            if i > 0 and page_delay > 0:
                await asyncio.sleep(page_delay)
            batch = unique_pairs[i : i + batch_size]
            query = " OR ".join(f"(from:{author} conversation_id:{conv})" for author, conv in batch)
            all_tweets.extend(await self.search_tweets(query, page_delay=page_delay))

        return all_tweets

    # =========================================================================
    # Bookmarks
    # =========================================================================

    async def get_bookmarks(self, count: int = 20, cursor: str | None = None) -> dict[str, Any]:
        variables = {
            "count": count,
            "includePromotedContent": False,
            "withDownvotePerspective": False,
            "withReactionsMetadata": False,
            "withReactionsPerspective": False,
        }
        if cursor:
            variables["cursor"] = cursor

        query_ids = self._get_query_ids("Bookmarks")
        success, data, error = await self._graphql_get_with_fallbacks(
            "Bookmarks", query_ids, variables, BOOKMARKS_FEATURES, field_toggles=ARTICLE_FIELD_TOGGLES
        )
        if not success:
            return {"success": False, "error": error, "tweets": []}

        instructions = data.get("data", {}).get("bookmark_timeline_v2", {}).get("timeline", {}).get("instructions", [])
        return {
            "success": True,
            "tweets": _parse_tweets_from_instructions(instructions),
            "next_cursor": _extract_cursor(instructions),
        }

    async def get_all_bookmarks(self, limit: int = 0, page_delay: float = 1.0, known_ids: set | None = None) -> dict[str, Any]:
        """Fetch all bookmarks with pagination. limit=0 means all. Stops early on known_ids hits."""
        return await self._paginate(self.get_bookmarks, limit=limit, page_delay=page_delay, known_ids=known_ids)

    # =========================================================================
    # Likes
    # =========================================================================

    async def get_current_user_id(self) -> str | None:
        if self.client_user_id:
            return self.client_user_id

        client = await self._get_client()
        try:
            self.request_count += 1
            response = await client.get("https://x.com/i/api/1.1/account/multi/list.json", headers=self._get_headers())
            if response.status_code == 200:
                users = response.json().get("users", [])
                if users:
                    user = users[0]
                    user_id = str(user.get("user_id", ""))
                    if user_id:
                        self.client_user_id = user_id
                        logger.info(f"Authenticated as @{user.get('screen_name', '')} (ID: {user_id})")
                        return user_id
        except Exception as e:
            logger.error(f"Failed to get current user: {e}")

        return None

    async def get_likes(self, count: int = 20, cursor: str | None = None) -> dict[str, Any]:
        user_id = await self.get_current_user_id()
        if not user_id:
            return {"success": False, "error": "Could not determine user ID", "tweets": []}

        variables = {
            "userId": user_id,
            "count": count,
            "includePromotedContent": False,
            "withClientEventToken": False,
            "withBirdwatchNotes": False,
            "withVoice": True,
        }
        if cursor:
            variables["cursor"] = cursor

        query_ids = self._get_query_ids("Likes")
        success, data, error = await self._graphql_get_with_fallbacks(
            "Likes", query_ids, variables, LIKES_FEATURES, field_toggles=ARTICLE_FIELD_TOGGLES
        )
        if not success:
            return {"success": False, "error": error, "tweets": []}

        instructions = (
            data.get("data", {}).get("user", {}).get("result", {}).get("timeline", {}).get("timeline", {}).get("instructions", [])
        )
        return {
            "success": True,
            "tweets": _parse_tweets_from_instructions(instructions),
            "next_cursor": _extract_cursor(instructions),
        }

    async def get_all_likes(self, limit: int = 0, page_delay: float = 1.0, known_ids: set | None = None) -> dict[str, Any]:
        """Fetch all likes with pagination. limit=0 means all. Stops early on known_ids hits."""
        return await self._paginate(self.get_likes, limit=limit, page_delay=page_delay, known_ids=known_ids)

    # =========================================================================
    # Pagination helper
    # =========================================================================

    async def _paginate(
        self,
        fetch_fn,
        limit: int = 0,
        page_delay: float = 1.0,
        max_pages: int = 100,
        known_ids: set | None = None,
        known_hit_threshold: int = 3,
    ) -> dict[str, Any]:
        """Generic paginator for timeline endpoints. If known_ids is given, stops after
        known_hit_threshold consecutive known tweets (guards against deleted/unliked
        tweets creating false stop points)."""
        all_tweets = []
        seen_ids = set()
        cursor = None
        unlimited = limit == 0
        consecutive_known = 0
        hit_threshold = False

        for page_num in range(max_pages):
            if page_num > 0 and page_delay > 0:
                await asyncio.sleep(page_delay)

            result = await fetch_fn(count=20, cursor=cursor)

            if not result.get("success"):
                if all_tweets:
                    return {"success": True, "tweets": all_tweets, "error": result.get("error")}
                return result

            added = 0
            for tweet in result.get("tweets", []):
                tweet_id = tweet.get("id")
                if not tweet_id or tweet_id in seen_ids:
                    continue

                if known_ids and tweet_id in known_ids:
                    consecutive_known += 1
                    if consecutive_known >= known_hit_threshold:
                        hit_threshold = True
                        logger.info(
                            f"Hit {known_hit_threshold} consecutive known tweets on page {page_num + 1}, "
                            f"stopping fetch ({len(all_tweets)} new tweets)"
                        )
                        break
                    continue

                consecutive_known = 0
                seen_ids.add(tweet_id)
                all_tweets.append(tweet)
                added += 1
                if not unlimited and len(all_tweets) >= limit:
                    break

            if hit_threshold or (not unlimited and len(all_tweets) >= limit):
                break

            next_cursor = result.get("next_cursor")
            if not next_cursor or next_cursor == cursor or added == 0:
                break
            cursor = next_cursor

        return {"success": True, "tweets": all_tweets}

    async def verify_credentials(self) -> bool:
        client = await self._get_client()
        try:
            self.request_count += 1
            response = await client.get("https://x.com/i/api/1.1/account/multi/list.json", headers=self._get_headers())
            if response.status_code != 200:
                logger.error(f"Credential verification failed: HTTP {response.status_code}")
                return False

            users = response.json().get("users", [])
            if not users:
                logger.error("Credential verification: no users in response")
                return False

            user = users[0]
            self.client_user_id = str(user.get("user_id", ""))
            logger.info(f"Credentials verified: @{user.get('screen_name', 'unknown')} (ID: {self.client_user_id})")
            return True
        except Exception as e:
            logger.error(f"Credential verification failed: {e}")
            return False


# =============================================================================
# Tweet parsing helpers (ported from bird's twitter-client-utils.ts)
# =============================================================================


def _unwrap_tweet_result(result: dict | None) -> dict | None:
    if not result:
        return None
    return result.get("tweet", result)


def _chain_authors(tweets: list[dict[str, Any]], focal_tweet_id: str) -> set[str]:
    """Authors on the focal tweet's ancestor path plus the focal author — the only
    authors whose tweets justify fetching more conversation pages."""
    by_id = {t["id"]: t for t in tweets}
    authors: set[str] = set()

    current = by_id.get(focal_tweet_id)
    while current:
        if author := current.get("author_username"):
            authors.add(author)
        current = by_id.get(current.get("in_reply_to_status_id"))

    return authors


_STATUS_URL_RE = re.compile(r"(?:twitter|x)\.com/[^/]+/status/(\d+)")


def _extract_linked_tweet_ids(result: dict, tweet_id: str, quoted_tweet_id: str | None) -> list[str]:
    """Tweet IDs linked via URL in the text, excluding the quote card and self-links.
    These are real references (screenshot-plus-link posts, 'see this' links) that
    don't surface as quoted_status_result."""
    url_entities = list(result.get("legacy", {}).get("entities", {}).get("urls", []))
    note = result.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {})
    url_entities += note.get("entity_set", {}).get("urls", [])

    linked = []
    for entity in url_entities:
        match = _STATUS_URL_RE.search(entity.get("expanded_url") or "")
        if match:
            lid = match.group(1)
            if lid not in (tweet_id, quoted_tweet_id) and lid not in linked:
                linked.append(lid)
    return linked


def _extract_media(result: dict | None) -> list[dict[str, Any]]:
    if not result:
        return []

    legacy = result.get("legacy", {})
    raw_media = legacy.get("extended_entities", {}).get("media", []) or legacy.get("entities", {}).get("media", [])
    if not raw_media:
        return []

    media = []
    for item in raw_media:
        media_type = item.get("type")
        media_url = item.get("media_url_https")
        if not media_type or not media_url:
            continue

        media_item = {"type": media_type, "url": media_url}

        large = item.get("sizes", {}).get("large", {})
        if large:
            media_item["width"] = large.get("w")
            media_item["height"] = large.get("h")

        if media_type in ("video", "animated_gif"):
            video_info = item.get("video_info", {})
            variants = video_info.get("variants", [])
            mp4_variants = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
            with_bitrate = sorted(
                (v for v in mp4_variants if v.get("bitrate") is not None), key=lambda v: v.get("bitrate", 0), reverse=True
            )
            selected = with_bitrate[0] if with_bitrate else (mp4_variants[0] if mp4_variants else None)
            if selected:
                media_item["video_url"] = selected["url"]

            duration = video_info.get("duration_millis")
            if isinstance(duration, int):
                media_item["duration_ms"] = duration

        media.append(media_item)

    return media


def _map_tweet_result(result: dict | None) -> dict[str, Any] | None:
    """Map a raw GraphQL tweet result to a simplified tweet dict."""
    result = _unwrap_tweet_result(result)
    if not result:
        return None

    user_result = result.get("core", {}).get("user_results", {}).get("result", {})
    user_legacy = user_result.get("legacy", {})
    user_core = user_result.get("core", {})
    username = user_legacy.get("screen_name") or user_core.get("screen_name")
    name = user_legacy.get("name") or user_core.get("name") or username
    user_id = user_result.get("rest_id")

    tweet_id = result.get("rest_id")
    if not tweet_id or not username:
        return None

    text = content.extract_tweet_text(result)
    if not text:
        return None

    media = _extract_media(result)
    legacy = result.get("legacy", {})

    retweeted_status = legacy.get("retweeted_status_result", {}).get("result")
    is_retweet = retweeted_status is not None
    retweeted_tweet_id = None
    if retweeted_status:
        unwrapped_rt = _unwrap_tweet_result(retweeted_status)
        if unwrapped_rt:
            retweeted_tweet_id = unwrapped_rt.get("rest_id")

    quoted_tweet = None
    quoted_tweet_id = None
    quoted_result = _unwrap_tweet_result(result.get("quoted_status_result", {}).get("result"))
    if quoted_result:
        quoted_tweet = _map_tweet_result(quoted_result)
        if quoted_tweet:
            quoted_tweet_id = quoted_tweet["id"]
            # payloads nest quotes only one level deep: the embedded dict can't
            # distinguish "has no quote" from "quote not included" — callers must
            # re-resolve it before trusting quoted_tweet_id/linked_tweet_ids
            quoted_tweet["shallow"] = True

    view_count = None
    views_count = result.get("views", {}).get("count")
    if views_count:
        try:
            view_count = int(views_count)
        except (ValueError, TypeError):
            pass

    return {
        "id": tweet_id,
        "text": text,
        "author_username": username,
        "author_name": name or username,
        "author_id": user_id,
        "author_protected": bool(user_legacy.get("protected")),
        "created_at": legacy.get("created_at"),
        "reply_count": legacy.get("reply_count"),
        "retweet_count": legacy.get("retweet_count"),
        "like_count": legacy.get("favorite_count"),
        "quote_count": legacy.get("quote_count"),
        "bookmark_count": legacy.get("bookmark_count"),
        "view_count": view_count,
        "conversation_id": legacy.get("conversation_id_str"),
        "in_reply_to_status_id": legacy.get("in_reply_to_status_id_str"),
        "quoted_tweet_id": quoted_tweet_id,
        "retweeted_tweet_id": retweeted_tweet_id,
        "is_retweet": is_retweet,
        "linked_tweet_ids": _extract_linked_tweet_ids(result, tweet_id, quoted_tweet_id) or None,
        "media": media if media else None,
        "quoted_tweet": quoted_tweet,
        "is_article": content.is_article(result),
    }


def _collect_tweet_results_from_entry(entry: dict) -> list[dict]:
    results = []
    content = entry.get("content", {})

    def push(result):
        if result and result.get("rest_id"):
            results.append(result)

    push(content.get("itemContent", {}).get("tweet_results", {}).get("result"))
    push(content.get("item", {}).get("itemContent", {}).get("tweet_results", {}).get("result"))
    for item in content.get("items", []):
        push(item.get("item", {}).get("itemContent", {}).get("tweet_results", {}).get("result"))
        push(item.get("itemContent", {}).get("tweet_results", {}).get("result"))
        push(item.get("content", {}).get("itemContent", {}).get("tweet_results", {}).get("result"))

    return results


def _find_tweet_in_instructions(instructions: list[dict], tweet_id: str) -> dict | None:
    for instruction in instructions:
        for entry in instruction.get("entries", []):
            for result in _collect_tweet_results_from_entry(entry):
                unwrapped = _unwrap_tweet_result(result)
                if unwrapped and unwrapped.get("rest_id") == tweet_id:
                    return unwrapped
    return None


# Timeline modules that carry tweets unrelated to the conversation (recommendations)
_NON_CONVERSATION_ENTRY_PREFIXES = ("tweetdetailrelatedtweets", "who-to-follow", "tweet-composer")


def _parse_tweets_from_instructions(
    instructions: list[dict], exclude_related: bool = False
) -> list[dict[str, Any]]:
    tweets = []
    seen = set()

    for instruction in instructions:
        for entry in instruction.get("entries", []):
            if exclude_related and (entry.get("entryId") or "").startswith(_NON_CONVERSATION_ENTRY_PREFIXES):
                continue
            for result in _collect_tweet_results_from_entry(entry):
                mapped = _map_tweet_result(result)
                if mapped and mapped["id"] not in seen:
                    seen.add(mapped["id"])
                    tweets.append(mapped)

    return tweets


_TOMBSTONE_ENTRY_ID_RE = re.compile(r"tweet-(\d+)$")


def _parse_tombstones_from_instructions(instructions: list[dict]) -> dict[str, str]:
    """Tombstoned tweets carry no rest_id; the ID only survives in the entryId."""
    tombstones: dict[str, str] = {}

    def check(entry_id: str | None, item_content: dict):
        result = item_content.get("tweet_results", {}).get("result")
        if not result or result.get("__typename") != "TweetTombstone":
            return
        match = _TOMBSTONE_ENTRY_ID_RE.search(entry_id or "")
        if not match:
            return
        reason = result.get("tombstone", {}).get("text", {}).get("text") or "unavailable"
        tombstones[match.group(1)] = reason

    for instruction in instructions:
        for entry in instruction.get("entries", []):
            content = entry.get("content", {})
            check(entry.get("entryId"), content.get("itemContent", {}))
            for item in content.get("items", []):
                check(item.get("entryId"), item.get("item", {}).get("itemContent", {}))

    return tombstones


def _extract_cursor(instructions: list[dict], cursor_type: str = "Bottom") -> str | None:
    for instruction in instructions:
        for entry in instruction.get("entries", []):
            content = entry.get("content", {})
            if content.get("cursorType") == cursor_type and isinstance(content.get("value"), str) and content["value"]:
                return content["value"]
    return None
