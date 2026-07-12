import logging
from pathlib import Path
from typing import Any

from social_archiver.core.media_kind import guess_mime_type
from social_archiver.core.milvus_manager import MilvusManager
from social_archiver.llm import local_embedder
from social_archiver.platforms.twitter.simple_tweet import SimpleTweet

logger = logging.getLogger(__name__)


class EmbeddingProcessor:
    """Processes an expanded tweet group: one VLM call with interleaved text + media."""

    def __init__(self, vlm_client: Any, milvus_manager: MilvusManager):
        self.vlm_client = vlm_client
        self.milvus_manager = milvus_manager

    async def process_expanded_group(
        self, tweets: list[SimpleTweet], media_paths: dict[str, list[Path]], category: str = "likes"
    ) -> tuple[bool, dict[str, str] | None]:
        """Process a full expanded group in one VLM call.

        Returns (success, {tweet_id: vlm_description} for tweets with media).
        """
        if not any(media_paths.values()):
            for tweet in tweets:
                await self._embed_text_only(tweet, category)
            return True, None

        parts = self._build_interleaved_parts(tweets, media_paths)
        if not parts:
            return False, None

        media_count = sum(1 for p in parts if p["type"] == "media")
        logger.info(f"VLM thread call: {len(tweets)} tweets, {media_count} media items")

        result = await self.vlm_client.describe_thread(parts)
        if not result:
            logger.error("VLM thread description failed")
            return False, None

        descriptions: dict[str, str] = {}
        for caption in result.captions:
            combined_parts = [caption.visual_description]
            if caption.visible_text:
                combined_parts.append(f"Visible text: {caption.visible_text}")
            if caption.speech_transcript:
                combined_parts.append(f"Speech: {caption.speech_transcript}")
            if caption.audio_description:
                combined_parts.append(f"Audio: {caption.audio_description}")
            combined = "\n\n".join(combined_parts)

            if caption.tweet_id in descriptions:
                descriptions[caption.tweet_id] += "\n\n---\n\n" + combined
            else:
                descriptions[caption.tweet_id] = combined

        logger.info(f"Got descriptions for {len(descriptions)} tweets ({len(result.captions)} media items)")

        for tweet in tweets:
            if desc := descriptions.get(tweet.id):
                await self._embed_with_description(tweet, category, desc)
            else:
                await self._embed_text_only(tweet, category)

        return True, descriptions

    def _build_interleaved_parts(self, tweets: list[SimpleTweet], media_paths: dict[str, list[Path]]) -> list[dict]:
        parts = []

        for tweet in tweets:
            label_parts = [f"[{tweet.origin}]"] if tweet.origin and tweet.origin not in ("liked", "bookmarked") else []
            text = (tweet.text or "").strip()
            author = f"@{tweet.author_username}"

            label = f"[tweet_id:{tweet.id}] {' '.join(label_parts)} {author}".strip()
            if text:
                label += f": {text}"

            if tweet.quoted_tweet_id:
                quoted = next((t for t in tweets if t.id == tweet.quoted_tweet_id), None)
                if quoted and (q_text := (quoted.text or "").strip()):
                    label += f"\n  ↳ Quotes @{quoted.author_username}: {q_text}"

            parts.append({"type": "text", "text": label, "tweet_id": tweet.id})

            for idx, path in enumerate(media_paths.get(tweet.id, [])):
                if mime := guess_mime_type(path):
                    parts.append({"type": "media", "path": path, "mime_type": mime, "tweet_id": tweet.id, "media_index": idx})

        return parts

    async def _embed_with_description(self, tweet: SimpleTweet, category: str, vlm_description: str):
        searchable_text = self._build_searchable_text(tweet.text, vlm_description)
        embedding = local_embedder.embed_document(searchable_text)

        await self.milvus_manager.insert_embedding(
            category=category,
            item_id=tweet.id,
            embedding=embedding,
            text=searchable_text,
            media_type="media",
            resource_index=None,
            metadata={"caption": tweet.text, "username": tweet.author_username},
        )

    async def _embed_text_only(self, tweet: SimpleTweet, category: str):
        text = (tweet.text or "").strip()
        if not text:
            return

        embedding = local_embedder.embed_document(text)

        await self.milvus_manager.insert_embedding(
            category=category,
            item_id=tweet.id,
            embedding=embedding,
            text=text,
            media_type="text",
            resource_index=None,
            metadata={"caption": tweet.text, "username": tweet.author_username},
        )

    def _build_searchable_text(self, tweet_text: str | None, vlm_description: str) -> str:
        parts = [tweet_text.strip()] if tweet_text and tweet_text.strip() else []
        parts.append(vlm_description)
        return "\n\n".join(parts)
