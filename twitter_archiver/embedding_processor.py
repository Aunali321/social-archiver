import logging
from typing import List, Optional, Tuple, Any, Dict
from pathlib import Path
from twitter_archiver.milvus_manager import MilvusManager
from twitter_archiver.database import Database
from twitter_archiver.simple_tweet import SimpleTweet
from insta_archiver import local_embedder

logger = logging.getLogger(__name__)


class EmbeddingProcessor:
    """Processes expanded tweet groups: one VLM call with interleaved text + media."""

    def __init__(self, vlm_client: Any, milvus_manager: MilvusManager, db: Database):
        self.vlm_client = vlm_client
        self.milvus_manager = milvus_manager
        self.db = db

    async def process_expanded_group(
        self,
        tweets: List[SimpleTweet],
        media_paths: Dict[str, List[Path]],
        category: str = "likes",
    ) -> Tuple[bool, Optional[Dict[str, str]]]:
        """Process a full expanded group in one VLM call.

        Args:
            tweets: All tweets in the expanded group (sorted by time).
            media_paths: Map of tweet_id -> list of downloaded media paths.
            category: Milvus collection category.

        Returns:
            (success, {tweet_id: vlm_description} for tweets with media)
        """
        has_any_media = any(paths for paths in media_paths.values())

        if not has_any_media:
            for tweet in tweets:
                await self._embed_text_only(tweet, category)
            return True, None

        # Build interleaved parts for VLM
        parts = self._build_interleaved_parts(tweets, media_paths)
        if not parts:
            return False, None

        media_count = sum(1 for p in parts if p["type"] == "media")
        logger.info(
            f"VLM thread call: {len(tweets)} tweets, {media_count} media items"
        )

        # One VLM call — returns structured ThreadCaptions
        result = await self.vlm_client.describe_thread(parts)
        if not result:
            logger.error("VLM thread description failed")
            return False, None

        # Build tweet_id -> description map from structured response
        descriptions = {}  # tweet_id -> combined description
        for caption in result.captions:
            tid = caption.tweet_id
            parts = [caption.visual_description]
            if caption.visible_text:
                parts.append(f"Visible text: {caption.visible_text}")
            if caption.speech_transcript:
                parts.append(f"Speech: {caption.speech_transcript}")
            if caption.audio_description:
                parts.append(f"Audio: {caption.audio_description}")
            combined = "\n\n".join(parts)
            if tid in descriptions:
                descriptions[tid] += "\n\n---\n\n" + combined
            else:
                descriptions[tid] = combined

        logger.info(
            f"Got descriptions for {len(descriptions)} tweets "
            f"({len(result.captions)} media items)"
        )

        # Embed each tweet
        for tweet in tweets:
            desc = descriptions.get(tweet.id)
            if desc:
                await self._embed_with_description(tweet, category, desc)
            else:
                await self._embed_text_only(tweet, category)

        return True, descriptions

    def _build_interleaved_parts(
        self,
        tweets: List[SimpleTweet],
        media_paths: Dict[str, List[Path]],
    ) -> List[dict]:
        """Build interleaved text + media parts list for VLM."""
        parts = []

        for tweet in tweets:
            # Build text label with tweet_id
            label_parts = []
            if tweet.origin and tweet.origin != "liked":
                label_parts.append(f"[{tweet.origin}]")

            text = (tweet.text or "").strip()
            author = f"@{tweet.author_username}"

            if text:
                label = f"[tweet_id:{tweet.id}] {' '.join(label_parts)} {author}: {text}".strip()
            else:
                label = f"[tweet_id:{tweet.id}] {' '.join(label_parts)} {author}".strip()

            # Add quoted tweet reference inline
            if tweet.quoted_tweet_id:
                quoted = next((t for t in tweets if t.id == tweet.quoted_tweet_id), None)
                if quoted:
                    q_text = (quoted.text or "").strip()
                    if q_text:
                        label += f"\n  ↳ Quotes @{quoted.author_username}: {q_text}"

            parts.append({"type": "text", "text": label, "tweet_id": tweet.id})

            # Add media for this tweet
            paths = media_paths.get(tweet.id, [])
            for idx, path in enumerate(paths):
                mime = self._get_mime_type(path)
                if mime:
                    parts.append({
                        "type": "media",
                        "path": path,
                        "mime_type": mime,
                        "tweet_id": tweet.id,
                        "media_index": idx,
                    })

        return parts

    async def _embed_with_description(
        self, tweet: SimpleTweet, category: str, vlm_description: str
    ):
        """Create embedding for a tweet that has a VLM description."""
        searchable_text = self._build_searchable_text(tweet.text, vlm_description)
        embedding = local_embedder.embed_document(searchable_text)

        metadata = {
            "tweet_text": tweet.text,
            "username": tweet.author_username,
            "tweet_id": tweet.id,
        }

        await self.milvus_manager.insert_embedding(
            category=category,
            tweet_id=tweet.id,
            embedding=embedding,
            text=searchable_text,
            media_type="media",
            resource_index=None,
            metadata=metadata,
        )

    async def _embed_text_only(self, tweet: SimpleTweet, category: str):
        """Embed a text-only tweet (no VLM)."""
        text = (tweet.text or "").strip()
        if not text:
            return

        embedding = local_embedder.embed_document(text)

        metadata = {
            "tweet_text": tweet.text,
            "username": tweet.author_username,
            "tweet_id": tweet.id,
        }

        await self.milvus_manager.insert_embedding(
            category=category,
            tweet_id=tweet.id,
            embedding=embedding,
            text=text,
            media_type="text",
            resource_index=None,
            metadata=metadata,
        )

    def _get_mime_type(self, path: Path) -> Optional[str]:
        suffix = path.suffix.lower()
        return {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".gif": "image/gif", ".webp": "image/webp",
            ".mp4": "video/mp4", ".mov": "video/quicktime",
            ".avi": "video/x-msvideo", ".webm": "video/webm",
        }.get(suffix)

    def _build_searchable_text(
        self, tweet_text: Optional[str], vlm_description: str
    ) -> str:
        parts = []
        if tweet_text and tweet_text.strip():
            parts.append(tweet_text.strip())
        parts.append(vlm_description)
        return "\n\n".join(parts)
