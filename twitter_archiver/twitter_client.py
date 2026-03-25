"""
Twitter GraphQL API client, ported from the bird TypeScript library.
Uses cookie-based authentication (auth_token + ct0) to access Twitter's
internal GraphQL endpoints.
"""
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
        """Get the current user's ID from settings endpoint."""
        if self.client_user_id:
            return self.client_user_id

        client = await self._get_client()
        try:
            response = await client.get(
                "https://x.com/i/api/1.1/account/settings.json",
                headers=self._get_headers(),
            )
            if response.status_code == 200:
                data = response.json()
                screen_name = data.get("screen_name")
                if screen_name:
                    logger.info(f"Authenticated as @{screen_name}")
                    # Get user ID from another endpoint
                    user_response = await client.get(
                        f"https://x.com/i/api/graphql/{QUERY_IDS.get('UserTweets', '')}/UserByScreenName",
                        headers=self._get_headers(),
                    )
                    # Fallback: use the viewer endpoint
                    viewer_url = "https://x.com/i/api/1.1/account/verify_credentials.json"
                    viewer_response = await client.get(
                        viewer_url, headers=self._get_headers()
                    )
                    if viewer_response.status_code == 200:
                        viewer_data = viewer_response.json()
                        user_id = str(viewer_data.get("id", ""))
                        if user_id:
                            self.client_user_id = user_id
                            logger.info(f"User ID: {user_id}")
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
    ) -> Dict[str, Any]:
        """Fetch all likes with pagination. limit=0 means all."""
        return await self._paginate(
            self.get_likes, limit=limit, page_delay=page_delay
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
    ) -> Dict[str, Any]:
        """Generic paginator for timeline endpoints."""
        import asyncio

        all_tweets = []
        seen_ids = set()
        cursor = None
        unlimited = limit == 0

        for page_num in range(max_pages):
            if page_num > 0 and page_delay > 0:
                await asyncio.sleep(page_delay)

            page_count = 20
            result = await fetch_fn(count=page_count, cursor=cursor)

            if not result.get("success"):
                if all_tweets:
                    # Return what we have so far
                    return {"success": True, "tweets": all_tweets, "error": result.get("error")}
                return result

            added = 0
            for tweet in result.get("tweets", []):
                tweet_id = tweet.get("id")
                if tweet_id and tweet_id not in seen_ids:
                    seen_ids.add(tweet_id)
                    all_tweets.append(tweet)
                    added += 1
                    if not unlimited and len(all_tweets) >= limit:
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
            response = await client.get(
                "https://x.com/i/api/1.1/account/verify_credentials.json",
                headers=self._get_headers(),
            )
            if response.status_code == 200:
                data = response.json()
                username = data.get("screen_name", "unknown")
                logger.info(f"Credentials verified: @{username}")
                self.client_user_id = str(data.get("id", ""))
                return True
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

    # Extract quoted tweet (one level deep)
    quoted_tweet = None
    quoted_result = _unwrap_tweet_result(
        result.get("quoted_status_result", {}).get("result")
    )
    if quoted_result:
        quoted_tweet = _map_tweet_result(quoted_result)

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
        "conversation_id": legacy.get("conversation_id_str"),
        "in_reply_to_status_id": legacy.get("in_reply_to_status_id_str"),
        "media": media if media else None,
        "quoted_tweet": quoted_tweet,
    }


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
