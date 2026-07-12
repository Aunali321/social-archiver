"""
Twitter GraphQL API client, ported from the bird TypeScript library.
Uses cookie-based authentication (auth_token + ct0) to access Twitter's
internal GraphQL endpoints.
"""
import asyncio
import json
import logging
import secrets
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx

from twitter_archiver import config

logger = logging.getLogger(__name__)

# Query IDs from bird reference (these rotate, but are good starting points)
QUERY_IDS = {
    "Bookmarks": "RV1g3b8n_SGOHwkqKYSCFw",
    "BookmarkFolderTimeline": "KJIQpsvxrTfRIlbaRIySHQ",
    "Likes": "JR2gceKucIKcVNB_9JkhsA",
    "UserTweets": "Wms1GvIiHXAPBaCr9KblaA",
    "TweetDetail": "97JF30KziU00483E_8elBA",
    "SearchTimeline": "M1jEez78PEfVfbQLvlWMvQ",
    "HomeTimeline": "edseUwk9sP5Phz__9TIRnA",
    "HomeLatestTimeline": "iOEZpOdfekFsxSlPQCQtPg",
}

# Fallback query IDs to try when primary ones get 404
FALLBACK_QUERY_IDS = {
    "Bookmarks": ["tmd4ifV8RHltzn8ymGg1aw"],
    "Likes": [],
    "UserTweets": [],
    "TweetDetail": [],
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

BOOKMARKS_FEATURES = {
    **TIMELINE_FEATURES,
    "graphql_timeline_v2_bookmark_timeline": True,
}

LIKES_FEATURES = TIMELINE_FEATURES.copy()

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


class TwitterClient:
    """Python port of bird's TwitterClient using cookie-based auth."""

    def __init__(
        self,
        auth_token: str = None,
        ct0: str = None,
    ):
        self.auth_token = auth_token or config.TWITTER_AUTH_TOKEN
        self.ct0 = ct0 or config.TWITTER_CT0
        self.client_uuid = str(uuid.uuid4())
        self.client_device_id = str(uuid.uuid4())
        self.client_user_id: Optional[str] = None
        self._http_client: Optional[httpx.AsyncClient] = None

    def _get_headers(self) -> Dict[str, str]:
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

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def _graphql_get(
        self,
        operation: str,
        query_id: str,
        variables: Dict[str, Any],
        features: Dict[str, bool],
        field_toggles: Optional[Dict[str, bool]] = None,
    ) -> Tuple[bool, Optional[Dict], Optional[str]]:
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
            response = await client.get(url, headers=self._get_headers())

            if response.status_code == 404:
                return False, None, f"HTTP 404 (query_id={query_id})"

            if response.status_code == 429:
                return False, None, "Rate limited (429)"

            if response.status_code != 200:
                text = response.text[:200]
                return False, None, f"HTTP {response.status_code}: {text}"

            data = response.json()

            if "errors" in data and data["errors"]:
                error_msg = ", ".join(e.get("message", "") for e in data["errors"])
                # Non-fatal if we still got data
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
        query_ids: List[str],
        variables: Dict[str, Any],
        features: Dict[str, bool],
        field_toggles: Optional[Dict[str, bool]] = None,
    ) -> Tuple[bool, Optional[Dict], Optional[str]]:
        """Try multiple query IDs for a GraphQL operation."""
        last_error = None
        for query_id in query_ids:
            success, data, error = await self._graphql_get(
                operation, query_id, variables, features, field_toggles
            )
            if success:
                return True, data, None
            last_error = error
            if error and "404" not in error:
                # Non-404 errors are likely not query ID related
                return False, None, error
        return False, None, last_error or "All query IDs failed"

    def _get_query_ids(self, operation: str) -> List[str]:
        """Get list of query IDs to try for an operation."""
        primary = QUERY_IDS.get(operation, "")
        fallbacks = FALLBACK_QUERY_IDS.get(operation, [])
        ids = [primary] + fallbacks if primary else fallbacks
        return [qid for qid in ids if qid]

    # =========================================================================
    # TweetDetail (conversation/thread fetching)
    # =========================================================================

    async def get_tweet_detail(
        self, tweet_id: str, cursor: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fetch TweetDetail for a tweet — returns the focal tweet + full
        conversation (threaded_conversation_with_injections_v2).

        This is the key endpoint for thread expansion. One call returns:
        - The focal tweet itself
        - All tweets in the conversation thread
        - Ancestors, siblings, descendants
        """
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
            "TweetDetail", query_ids, variables, TWEET_DETAIL_FEATURES,
            field_toggles=TWEET_DETAIL_FIELD_TOGGLES,
        )

        if not success:
            # Try POST fallback like bird does on 404
            if error and "404" in error:
                for query_id in query_ids:
                    post_success, post_data, post_error = await self._graphql_post(
                        "TweetDetail", query_id, variables, TWEET_DETAIL_FEATURES,
                        field_toggles=TWEET_DETAIL_FIELD_TOGGLES,
                    )
                    if post_success:
                        return self._parse_tweet_detail_response(post_data, tweet_id)
            raise RuntimeError(f"TweetDetail failed for {tweet_id}: {error}")

        return self._parse_tweet_detail_response(data, tweet_id)

    async def _graphql_post(
        self,
        operation: str,
        query_id: str,
        variables: Dict[str, Any],
        features: Dict[str, bool],
        field_toggles: Optional[Dict[str, bool]] = None,
    ) -> Tuple[bool, Optional[Dict], Optional[str]]:
        """Make a GraphQL POST request (fallback for 404 on GET)."""
        url = f"{config.TWITTER_API_BASE}/{query_id}/{operation}"
        body = {"variables": variables, "features": features, "queryId": query_id}
        if field_toggles:
            body["fieldToggles"] = field_toggles

        client = await self._get_client()
        try:
            response = await client.post(url, headers=self._get_headers(), json=body)
            if response.status_code == 404:
                return False, None, f"HTTP 404 POST (query_id={query_id})"
            if response.status_code == 429:
                return False, None, "Rate limited (429)"
            if response.status_code != 200:
                text = response.text[:200]
                return False, None, f"HTTP {response.status_code}: {text}"
            data = response.json()
            if "errors" in data and data["errors"]:
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

    def _parse_tweet_detail_response(
        self, data: Dict, focal_tweet_id: str
    ) -> Dict[str, Any]:
        """Parse TweetDetail response into structured result."""
        data_root = data.get("data", {})

        # Parse focal tweet from tweetResult
        focal_result = data_root.get("tweetResult", {}).get("result")

        # Parse all tweets from threaded conversation
        instructions = (
            data_root
            .get("threaded_conversation_with_injections_v2", {})
            .get("instructions", [])
        )

        all_tweets = _parse_tweets_from_instructions(instructions)

        # If focal tweet not in conversation results, parse it separately
        if focal_result:
            focal_mapped = _map_tweet_result(focal_result)
            if focal_mapped:
                # Check if already in all_tweets
                existing_ids = {t["id"] for t in all_tweets}
                if focal_mapped["id"] not in existing_ids:
                    all_tweets.insert(0, focal_mapped)

        # Also try to find focal tweet in instructions if tweetResult was empty
        if not focal_result:
            focal_result = _find_tweet_in_instructions(instructions, focal_tweet_id)
            if focal_result:
                focal_mapped = _map_tweet_result(focal_result)

        next_cursor = _extract_cursor(instructions)

        return {
            "success": True,
            "tweets": all_tweets,
            "next_cursor": next_cursor,
        }

    async def get_thread(
        self, tweet_id: str, max_pages: int = 5, page_delay: float = 1.0
    ) -> List[Dict[str, Any]]:
        """
        Fetch the full conversation thread for a tweet.
        Returns all tweets in the conversation, paginating if needed.
        """
        all_tweets = []
        seen_ids = set()
        cursor = None

        for page in range(max_pages):
            if page > 0 and page_delay > 0:
                await asyncio.sleep(page_delay)

            result = await self.get_tweet_detail(tweet_id, cursor=cursor)
            tweets = result.get("tweets", [])

            added = 0
            for tweet in tweets:
                tid = tweet.get("id")
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    all_tweets.append(tweet)
                    added += 1

            next_cursor = result.get("next_cursor")
            if not next_cursor or next_cursor == cursor or added == 0:
                break
            cursor = next_cursor

        return all_tweets

    # =========================================================================
    # Bookmarks
    # =========================================================================

    async def get_bookmarks(
        self, count: int = 20, cursor: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fetch a page of bookmarks."""
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
            "Bookmarks", query_ids, variables, BOOKMARKS_FEATURES
        )

        if not success:
            return {"success": False, "error": error, "tweets": []}

        instructions = (
            data.get("data", {})
            .get("bookmark_timeline_v2", {})
            .get("timeline", {})
            .get("instructions", [])
        )

        tweets = _parse_tweets_from_instructions(instructions)
        next_cursor = _extract_cursor(instructions)

        return {
            "success": True,
            "tweets": tweets,
            "next_cursor": next_cursor,
        }

    async def get_all_bookmarks(
        self,
        limit: int = 0,
        page_delay: float = 1.0,
    ) -> Dict[str, Any]:
        """Fetch all bookmarks with pagination. limit=0 means all."""
        return await self._paginate(
            self.get_bookmarks, limit=limit, page_delay=page_delay
        )

    # =========================================================================
    # Likes
    # =========================================================================

    async def get_current_user_id(self) -> Optional[str]:
        """Get the current user's ID."""
        if self.client_user_id:
            return self.client_user_id

        client = await self._get_client()
        try:
            response = await client.get(
                "https://x.com/i/api/1.1/account/multi/list.json",
                headers=self._get_headers(),
            )
            if response.status_code == 200:
                users = response.json().get("users", [])
                if users:
                    user = users[0]
                    user_id = str(user.get("user_id", ""))
                    screen_name = user.get("screen_name", "")
                    if user_id:
                        self.client_user_id = user_id
                        logger.info(f"Authenticated as @{screen_name} (ID: {user_id})")
                        return user_id
        except Exception as e:
            logger.error(f"Failed to get current user: {e}")

        return None

    async def get_likes(
        self, count: int = 20, cursor: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fetch a page of liked tweets."""
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
            "Likes", query_ids, variables, LIKES_FEATURES
        )

        if not success:
            return {"success": False, "error": error, "tweets": []}

        instructions = (
            data.get("data", {})
            .get("user", {})
            .get("result", {})
            .get("timeline", {})
            .get("timeline", {})
            .get("instructions", [])
        )

        tweets = _parse_tweets_from_instructions(instructions)
        next_cursor = _extract_cursor(instructions)

        return {
            "success": True,
            "tweets": tweets,
            "next_cursor": next_cursor,
        }

    async def get_all_likes(
        self,
        limit: int = 0,
        page_delay: float = 1.0,
        known_ids: Optional[set] = None,
    ) -> Dict[str, Any]:
        """Fetch all likes with pagination. limit=0 means all.
        If known_ids is provided, stops fetching when a known tweet is encountered."""
        return await self._paginate(
            self.get_likes, limit=limit, page_delay=page_delay, known_ids=known_ids
        )

    # =========================================================================
    # Pagination helper
    # =========================================================================

    async def _paginate(
        self,
        fetch_fn,
        limit: int = 0,
        page_delay: float = 1.0,
        max_pages: int = 100,
        known_ids: Optional[set] = None,
        known_hit_threshold: int = 3,
    ) -> Dict[str, Any]:
        """Generic paginator for timeline endpoints.
        If known_ids is provided, stops after hitting known_hit_threshold consecutive
        known tweets (guards against deleted/unliked tweets creating false stop points)."""
        all_tweets = []
        seen_ids = set()
        cursor = None
        unlimited = limit == 0
        consecutive_known = 0
        hit_threshold = False

        for page_num in range(max_pages):
            if page_num > 0 and page_delay > 0:
                await asyncio.sleep(page_delay)

            page_count = 20
            result = await fetch_fn(count=page_count, cursor=cursor)

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
                            f"Hit {known_hit_threshold} consecutive known tweets "
                            f"on page {page_num + 1}, stopping fetch "
                            f"({len(all_tweets)} new tweets)"
                        )
                        break
                    continue

                # Reset counter when we see a new tweet
                consecutive_known = 0
                seen_ids.add(tweet_id)
                all_tweets.append(tweet)
                added += 1
                if not unlimited and len(all_tweets) >= limit:
                    break

            if hit_threshold:
                break

            if not unlimited and len(all_tweets) >= limit:
                break

            next_cursor = result.get("next_cursor")
            if not next_cursor or next_cursor == cursor or added == 0:
                break

            cursor = next_cursor

        return {"success": True, "tweets": all_tweets}

    async def verify_credentials(self) -> bool:
        """Verify that the provided credentials are valid."""
        client = await self._get_client()
        try:
            # v1.1 endpoints are dead (404), use multi/list instead
            response = await client.get(
                "https://x.com/i/api/1.1/account/multi/list.json",
                headers=self._get_headers(),
            )
            if response.status_code == 200:
                users = response.json().get("users", [])
                if users:
                    user = users[0]
                    username = user.get("screen_name", "unknown")
                    user_id = str(user.get("user_id", ""))
                    self.client_user_id = user_id
                    logger.info(f"Credentials verified: @{username} (ID: {user_id})")
                    return True
                logger.error("Credential verification: no users in response")
                return False
            else:
                logger.error(f"Credential verification failed: HTTP {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Credential verification failed: {e}")
            return False


# =============================================================================
# Tweet parsing helpers (ported from bird's twitter-client-utils.ts)
# =============================================================================


def _unwrap_tweet_result(result: Optional[Dict]) -> Optional[Dict]:
    """Unwrap a tweet result, handling the 'tweet' wrapper."""
    if not result:
        return None
    if "tweet" in result:
        return result["tweet"]
    return result


def _extract_tweet_text(result: Optional[Dict]) -> Optional[str]:
    """Extract tweet text, handling note tweets and articles."""
    if not result:
        return None

    # Try note tweet first (long-form tweets)
    note = (
        result.get("note_tweet", {})
        .get("note_tweet_results", {})
        .get("result", {})
    )
    if note:
        for key in ["text", "richtext", "rich_text"]:
            val = note.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, dict):
                text = val.get("text", "")
                if text and text.strip():
                    return text.strip()
        content = note.get("content", {})
        for key in ["text", "richtext", "rich_text"]:
            val = content.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, dict):
                text = val.get("text", "")
                if text and text.strip():
                    return text.strip()

    # Fall back to legacy full_text
    full_text = result.get("legacy", {}).get("full_text", "")
    return full_text if full_text else None


def _extract_media(result: Optional[Dict]) -> List[Dict[str, Any]]:
    """Extract media items from a tweet result."""
    if not result:
        return []

    legacy = result.get("legacy", {})
    raw_media = (
        legacy.get("extended_entities", {}).get("media", [])
        or legacy.get("entities", {}).get("media", [])
    )

    if not raw_media:
        return []

    media = []
    for item in raw_media:
        media_type = item.get("type")
        media_url = item.get("media_url_https")
        if not media_type or not media_url:
            continue

        media_item = {
            "type": media_type,  # photo, video, animated_gif
            "url": media_url,
        }

        # Get dimensions
        sizes = item.get("sizes", {})
        large = sizes.get("large", {})
        if large:
            media_item["width"] = large.get("w")
            media_item["height"] = large.get("h")

        # Extract video URL
        if media_type in ("video", "animated_gif"):
            video_info = item.get("video_info", {})
            variants = video_info.get("variants", [])
            mp4_variants = [
                v for v in variants
                if v.get("content_type") == "video/mp4" and v.get("url")
            ]
            # Sort by bitrate (highest first), falling back to those without bitrate
            with_bitrate = sorted(
                [v for v in mp4_variants if v.get("bitrate") is not None],
                key=lambda v: v.get("bitrate", 0),
                reverse=True,
            )
            selected = with_bitrate[0] if with_bitrate else (mp4_variants[0] if mp4_variants else None)
            if selected:
                media_item["video_url"] = selected["url"]

            duration = video_info.get("duration_millis")
            if isinstance(duration, int):
                media_item["duration_ms"] = duration

        media.append(media_item)

    return media


def _map_tweet_result(result: Optional[Dict]) -> Optional[Dict[str, Any]]:
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

    text = _extract_tweet_text(result)
    if not text:
        return None

    media = _extract_media(result)
    legacy = result.get("legacy", {})

    # Detect retweet
    retweeted_status = legacy.get("retweeted_status_result", {}).get("result")
    is_retweet = retweeted_status is not None
    retweeted_tweet_id = None
    if retweeted_status:
        unwrapped_rt = _unwrap_tweet_result(retweeted_status)
        if unwrapped_rt:
            retweeted_tweet_id = unwrapped_rt.get("rest_id")

    # Extract quoted tweet (recursive)
    quoted_tweet = None
    quoted_tweet_id = None
    quoted_result = _unwrap_tweet_result(
        result.get("quoted_status_result", {}).get("result")
    )
    if quoted_result:
        quoted_tweet = _map_tweet_result(quoted_result)
        if quoted_tweet:
            quoted_tweet_id = quoted_tweet["id"]

    # Extract view count
    view_count = None
    views = result.get("views", {})
    if views and views.get("count"):
        try:
            view_count = int(views["count"])
        except (ValueError, TypeError):
            pass

    return {
        "id": tweet_id,
        "text": text,
        "author_username": username,
        "author_name": name or username,
        "author_id": user_id,
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
        "media": media if media else None,
        "quoted_tweet": quoted_tweet,
    }


def _find_tweet_in_instructions(
    instructions: List[Dict], tweet_id: str
) -> Optional[Dict]:
    """Find a specific tweet result in instructions by ID."""
    for instruction in instructions:
        for entry in instruction.get("entries", []):
            results = _collect_tweet_results_from_entry(entry)
            for result in results:
                unwrapped = _unwrap_tweet_result(result)
                if unwrapped and unwrapped.get("rest_id") == tweet_id:
                    return unwrapped
    return None


def _collect_tweet_results_from_entry(entry: Dict) -> List[Dict]:
    """Collect all tweet results from a timeline entry."""
    results = []
    content = entry.get("content", {})

    def push(result):
        if result and result.get("rest_id"):
            results.append(result)

    # Direct itemContent
    push(content.get("itemContent", {}).get("tweet_results", {}).get("result"))
    # Nested item
    push(
        content.get("item", {})
        .get("itemContent", {})
        .get("tweet_results", {})
        .get("result")
    )
    # Module items (conversation threads, etc.)
    for item in content.get("items", []):
        push(
            item.get("item", {})
            .get("itemContent", {})
            .get("tweet_results", {})
            .get("result")
        )
        push(item.get("itemContent", {}).get("tweet_results", {}).get("result"))
        push(
            item.get("content", {})
            .get("itemContent", {})
            .get("tweet_results", {})
            .get("result")
        )

    return results


def _parse_tweets_from_instructions(instructions: List[Dict]) -> List[Dict[str, Any]]:
    """Parse tweets from GraphQL timeline instructions."""
    tweets = []
    seen = set()

    for instruction in instructions:
        for entry in instruction.get("entries", []):
            results = _collect_tweet_results_from_entry(entry)
            for result in results:
                mapped = _map_tweet_result(result)
                if mapped and mapped["id"] not in seen:
                    seen.add(mapped["id"])
                    tweets.append(mapped)

    return tweets


def _extract_cursor(
    instructions: List[Dict], cursor_type: str = "Bottom"
) -> Optional[str]:
    """Extract pagination cursor from timeline instructions."""
    for instruction in instructions:
        for entry in instruction.get("entries", []):
            content = entry.get("content", {})
            if (
                content.get("cursorType") == cursor_type
                and isinstance(content.get("value"), str)
                and content["value"]
            ):
                return content["value"]
    return None
