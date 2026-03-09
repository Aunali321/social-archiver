import logging
import asyncio
from typing import List, Optional, Tuple, Any
from pathlib import Path
from insta_archiver.milvus_manager import MilvusManager
from insta_archiver.simple_media import SimpleMedia
from insta_archiver import local_embedder

logger = logging.getLogger(__name__)


class EmbeddingProcessor:
    """Processes media: VLM description + local embeddings."""

    def __init__(self, vlm_client: Any, milvus_manager: MilvusManager):
        self.vlm_client = vlm_client
        self.milvus_manager = milvus_manager

    async def process_media(
        self, media_pk: int, media: SimpleMedia, category: str, local_paths: List[Path]
    ) -> Tuple[bool, Optional[str]]:
        """Generate VLM description, create embedding, store in Milvus."""
        try:
            if media.media_type == 8:
                return await self._process_album(media_pk, media, category, local_paths)
            else:
                return await self._process_single(
                    media_pk, media, category, local_paths[0]
                )
        except Exception as e:
            logger.error(f"Failed to process embeddings for {media_pk}: {e}")
            return False, None

    async def _process_single(
        self, media_pk: int, media: SimpleMedia, category: str, media_path: Path
    ) -> Tuple[bool, Optional[str]]:
        if media.media_type == 1:
            media_type_str = "image"
        elif media.media_type == 2:
            media_type_str = "video"
        else:
            logger.warning(f"Unknown media type {media.media_type} for {media_pk}")
            return False, None

        # Get VLM description
        vlm_description = await self.vlm_client.describe_media(
            media_path, media_type_str
        )
        if not vlm_description:
            logger.error(f"Failed to get VLM description for {media_pk}")
            return False, None

        # Combine caption + description for embedding
        searchable_text = self._build_searchable_text(
            media.caption_text, vlm_description
        )

        # Generate embedding using local model
        embedding = local_embedder.embed_document(searchable_text)

        # Store in Milvus
        metadata = {
            "caption": media.caption_text,
            "username": media.user.username,
            "code": media.code,
        }

        success = await self.milvus_manager.insert_embedding(
            category=category,
            media_pk=media_pk,
            embedding=embedding,
            text=searchable_text,
            media_type=media.media_type,
            resource_index=None,
            metadata=metadata,
        )

        return success, vlm_description

    async def _process_album(
        self, media_pk: int, media: SimpleMedia, category: str, local_paths: List[Path]
    ) -> Tuple[bool, Optional[str]]:
        if not local_paths:
            logger.error(f"No local paths for album {media_pk}")
            return False, None

        if media.resources and len(local_paths) != len(media.resources):
            logger.warning(f"Path count mismatch for album {media_pk}")

        tasks = []
        for idx, path in enumerate(local_paths):
            resource = media.resources[idx] if idx < len(media.resources) else None
            tasks.append(
                self._process_album_item(
                    media_pk, media, category, resource, path, idx, len(local_paths)
                )
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

    def _infer_media_type_from_path(self, path: Path) -> Optional[int]:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            return 1
        elif suffix in {".mp4", ".mov", ".avi", ".webm", ".mkv"}:
            return 2
        return None

    async def _process_album_item(
        self,
        media_pk: int,
        media: SimpleMedia,
        category: str,
        resource,
        path: Path,
        index: int,
        album_size: int,
    ) -> Tuple[bool, Optional[str]]:
        if resource is not None:
            resource_media_type = resource.media_type
        else:
            resource_media_type = self._infer_media_type_from_path(path)
            if resource_media_type is None:
                logger.warning(f"Cannot determine media type for {path}")
                return False, None

        media_type_str = "image" if resource_media_type == 1 else "video"

        # Get VLM description
        vlm_description = await self.vlm_client.describe_media(path, media_type_str)
        if not vlm_description:
            logger.error(f"Failed to get VLM description for {media_pk} item {index}")
            return False, None

        searchable_text = self._build_searchable_text(
            media.caption_text, vlm_description
        )
        embedding = local_embedder.embed_document(searchable_text)

        metadata = {
            "caption": media.caption_text,
            "username": media.user.username,
            "code": media.code,
            "album_size": album_size,
        }

        success = await self.milvus_manager.insert_embedding(
            category=category,
            media_pk=media_pk,
            embedding=embedding,
            text=searchable_text,
            media_type=resource_media_type,
            resource_index=index,
            metadata=metadata,
        )

        return success, vlm_description

    def _build_searchable_text(
        self, caption: Optional[str], vlm_description: str
    ) -> str:
        parts = []
        if caption and caption.strip():
            parts.append(caption.strip())
        parts.append(vlm_description)
        return "\n\n".join(parts)
