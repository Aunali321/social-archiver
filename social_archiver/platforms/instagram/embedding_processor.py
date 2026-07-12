import asyncio
import logging
from pathlib import Path
from typing import Any

from social_archiver.core.media_kind import guess_media_kind
from social_archiver.core.milvus_manager import MilvusManager
from social_archiver.llm import local_embedder
from social_archiver.platforms.instagram.simple_media import SimpleMedia

logger = logging.getLogger(__name__)

_VLM_MEDIA_TYPE = {"photo": "image", "video": "video"}


class EmbeddingProcessor:
    """VLM description + local embedding, per media item."""

    def __init__(self, vlm_client: Any, milvus_manager: MilvusManager):
        self.vlm_client = vlm_client
        self.milvus_manager = milvus_manager

    async def process_media(
        self, item_id: str, media: SimpleMedia, category: str, local_paths: list[Path]
    ) -> tuple[bool, str | None]:
        try:
            if media.media_type == 8:
                return await self._process_album(item_id, media, category, local_paths)
            return await self._process_single(item_id, media, category, local_paths[0])
        except Exception as e:
            logger.error(f"Failed to process embeddings for {item_id}: {e}")
            return False, None

    async def _process_single(
        self, item_id: str, media: SimpleMedia, category: str, media_path: Path
    ) -> tuple[bool, str | None]:
        kind = guess_media_kind(media_path)
        vlm_description = await self.vlm_client.describe_media(media_path, _VLM_MEDIA_TYPE[kind])
        if not vlm_description:
            logger.error(f"Failed to get VLM description for {item_id}")
            return False, None

        searchable_text = self._build_searchable_text(media.caption_text, vlm_description)
        embedding = local_embedder.embed_document(searchable_text)

        success = await self.milvus_manager.insert_embedding(
            category=category,
            item_id=item_id,
            embedding=embedding,
            text=searchable_text,
            media_type=kind,
            resource_index=None,
            metadata={"caption": media.caption_text, "username": media.user.username, "code": media.code},
        )
        return success, vlm_description

    async def _process_album(
        self, item_id: str, media: SimpleMedia, category: str, local_paths: list[Path]
    ) -> tuple[bool, str | None]:
        if not local_paths:
            logger.error(f"No local paths for album {item_id}")
            return False, None

        tasks = [
            self._process_album_item(item_id, media, category, path, idx, len(local_paths))
            for idx, path in enumerate(local_paths)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        descriptions = [desc for r in results if isinstance(r, tuple) and (desc := r[1])]
        all_success = all(isinstance(r, tuple) and r[0] for r in results)

        combined = "\n\n---\n\n".join(descriptions) if descriptions else None
        return all_success, combined

    async def _process_album_item(
        self, item_id: str, media: SimpleMedia, category: str, path: Path, index: int, album_size: int
    ) -> tuple[bool, str | None]:
        kind = guess_media_kind(path)
        vlm_description = await self.vlm_client.describe_media(path, _VLM_MEDIA_TYPE[kind])
        if not vlm_description:
            logger.error(f"Failed to get VLM description for {item_id} item {index}")
            return False, None

        searchable_text = self._build_searchable_text(media.caption_text, vlm_description)
        embedding = local_embedder.embed_document(searchable_text)

        success = await self.milvus_manager.insert_embedding(
            category=category,
            item_id=item_id,
            embedding=embedding,
            text=searchable_text,
            media_type=kind,
            resource_index=index,
            metadata={
                "caption": media.caption_text,
                "username": media.user.username,
                "code": media.code,
                "album_size": album_size,
            },
        )
        return success, vlm_description

    def _build_searchable_text(self, caption: str | None, vlm_description: str) -> str:
        parts = [caption.strip()] if caption and caption.strip() else []
        parts.append(vlm_description)
        return "\n\n".join(parts)
