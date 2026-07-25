import asyncio
import logging
from pathlib import Path

from social_archiver.core.database import Database, Item
from social_archiver.core.jobs import ensure_media
from social_archiver.core.media_kind import guess_media_kind
from social_archiver.core.milvus_manager import MilvusManager
from social_archiver.llm import embed_client
from social_archiver.llm.vertex_client import VertexVLMClient
from social_archiver.platforms.instagram.port import PLATFORM, InstagramPort

logger = logging.getLogger(__name__)

EMBED_CONCURRENCY = 6

_VLM_MEDIA_TYPE = {"photo": "image", "video": "video"}


class EmbedJob:
    """Generates VLM descriptions and search embeddings for archived posts,
    one post at a time (album files individually), a few posts in parallel.
    Purely DB-driven: an interrupted run resumes with whatever is pending."""

    def __init__(self, db: Database, vlm: VertexVLMClient, milvus: MilvusManager, port: InstagramPort):
        self.db = db
        self.vlm = vlm
        self.milvus = milvus
        self.port = port

    async def run(self, retry_failed: bool = False):
        for category in self.port.chats:
            pending = await self.db.pending_embed(PLATFORM, category, retry_failed)
            if not pending:
                continue

            logger.info(f"Embedding {len(pending)} {category} items")
            semaphore = asyncio.Semaphore(EMBED_CONCURRENCY)
            async with asyncio.TaskGroup() as tasks:
                for item in pending:
                    tasks.create_task(self._embed_item(item, semaphore))

    async def _embed_item(self, item: Item, semaphore: asyncio.Semaphore):
        async with semaphore:
            try:
                description = await self._describe_and_index(item)
                await self.db.mark_embedded(item.item_id, description)
                logger.info(f"Embedded {item.item_id} (@{item.author_username})")
            except Exception as e:
                logger.error(f"Embedding failed for {item.item_id}: {e}")
                await self.db.mark_embed_failed(item.item_id, str(e))

    async def _describe_and_index(self, item: Item) -> str:
        paths = await ensure_media(self.db, self.port, item)
        is_album = len(paths) > 1

        descriptions = []
        for index, path in enumerate(paths):
            descriptions.append(await self._describe_one(item, path, index if is_album else None))
        return "\n\n---\n\n".join(descriptions)

    async def _describe_one(self, item: Item, path: Path, resource_index: int | None) -> str:
        kind = guess_media_kind(path)
        description = await self.vlm.describe_media(path, _VLM_MEDIA_TYPE[kind])
        if not description:
            raise RuntimeError(f"VLM returned no description for {path.name}")

        searchable = "\n\n".join(section for section in ((item.text or "").strip(), description) if section)
        await self.milvus.insert_embedding(
            category=item.category,
            item_id=item.item_id,
            embedding=embed_client.embed_document(searchable),
            text=searchable,
            media_type=kind,
            resource_index=resource_index,
            caption=item.text,
            username=item.author_username,
            code=_shortcode(item),
        )
        return description


def _shortcode(item: Item) -> str:
    return item.post_url.rstrip("/").rsplit("/", 1)[-1]
