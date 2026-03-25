import logging
import asyncio
from typing import List, Optional, Tuple, Any
from pathlib import Path
from twitter_archiver.milvus_manager import MilvusManager
from twitter_archiver.simple_tweet import SimpleTweet
from insta_archiver import local_embedder

logger = logging.getLogger(__name__)


class EmbeddingProcessor:
    """Processes tweets: VLM description + local embeddings."""

    def __init__(self, vlm_client: Any, milvus_manager: MilvusManager):
        self.vlm_client = vlm_client
        self.milvus_manager = milvus_manager

    async def process_tweet(
        self, tweet: SimpleTweet, category: str, local_paths: List[Path]
    ) -> Tuple[bool, Optional[str]]:
        """Generate VLM description (for media), create embedding, store in Milvus."""
        try:
            if local_paths:
                return await self._process_with_media(tweet, category, local_paths)
            else:
                return await self._process_text_only(tweet, category)
        except Exception as e:
            logger.error(f"Failed to process embeddings for {tweet.id}: {e}")
            return False, None

    async def _process_text_only(
        self, tweet: SimpleTweet, category: str
    ) -> Tuple[bool, Optional[str]]:
        """Process a text-only tweet (no VLM needed)."""
        searchable_text = tweet.text
        if not searchable_text or not searchable_text.strip():
            return False, None

        embedding = local_embedder.embed_document(searchable_text)

        metadata = {
            "tweet_text": tweet.text,
            "username": tweet.author_username,
            "tweet_id": tweet.id,
        }

        success = await self.milvus_manager.insert_embedding(
            category=category,
            tweet_id=tweet.id,
            embedding=embedding,
            text=searchable_text,
            media_type="text",
            resource_index=None,
            metadata=metadata,
        )

        return success, None

    async def _process_with_media(
        self, tweet: SimpleTweet, category: str, local_paths: List[Path]
    ) -> Tuple[bool, Optional[str]]:
        """Process a tweet with media attachments."""
        if len(local_paths) == 1:
            return await self._process_single_media(
                tweet, category, local_paths[0], 0
            )

        # Multiple media: process each in parallel
        tasks = []
        for idx, path in enumerate(local_paths):
            tasks.append(
                self._process_single_media(tweet, category, path, idx)
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_descriptions = []
        all_success = True

        for r in results:
            if isinstance(r, Exception):
                all_success = False
            elif isinstance(r, tuple):
                success, desc = r
                if not success:
                    all_success = False
                if desc:
                    all_descriptions.append(desc)
            else:
                all_success = False

        combined_description = (
            "\n\n---\n\n".join(all_descriptions) if all_descriptions else None
        )
        return all_success, combined_description

    async def _process_single_media(
        self, tweet: SimpleTweet, category: str, media_path: Path, index: int
    ) -> Tuple[bool, Optional[str]]:
        media_type_str = self._infer_media_type(media_path)
        if not media_type_str:
            logger.warning(f"Cannot determine media type for {media_path}")
            return False, None

        vlm_description = await self.vlm_client.describe_media(
            media_path, media_type_str
        )
        if not vlm_description:
            logger.error(f"Failed to get VLM description for {tweet.id} item {index}")
            return False, None

        searchable_text = self._build_searchable_text(tweet.text, vlm_description)
        embedding = local_embedder.embed_document(searchable_text)

        metadata = {
            "tweet_text": tweet.text,
            "username": tweet.author_username,
            "tweet_id": tweet.id,
        }

        success = await self.milvus_manager.insert_embedding(
            category=category,
            tweet_id=tweet.id,
            embedding=embedding,
            text=searchable_text,
            media_type=media_type_str,
            resource_index=index if len(tweet.media) > 1 else None,
            metadata=metadata,
        )

        return success, vlm_description

    def _infer_media_type(self, path: Path) -> Optional[str]:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            return "image"
        elif suffix in {".mp4", ".mov", ".avi", ".webm", ".mkv"}:
            return "video"
        return None

    def _build_searchable_text(
        self, tweet_text: Optional[str], vlm_description: str
    ) -> str:
        parts = []
        if tweet_text and tweet_text.strip():
            parts.append(tweet_text.strip())
        parts.append(vlm_description)
        return "\n\n".join(parts)
