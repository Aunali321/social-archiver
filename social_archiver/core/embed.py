"""Vector search indexing: the stage after captioning.

Once an item is captioned (`caption_status = done`), this job embeds its caption
and text on the embedding server and inserts the vector into Milvus, marking
`embed_status`. It is deliberately separate from captioning: the expensive VLM
work is already done and recorded, so indexing can run later, on different
hardware (the embedding model wants a GPU), and retry on its own without
re-captioning anything.
"""

import logging
from typing import Protocol

from social_archiver.core.database import Database, Item
from social_archiver.core.milvus_manager import MilvusManager
from social_archiver.llm import embed_client

logger = logging.getLogger(__name__)


class IndexPort(Protocol):
    platform: str
    chats: dict[str, int]

    def embed_category(self, item: Item, loop_category: str) -> str:
        """Which Milvus collection (category) to index this item under."""


class IndexJob:
    """Embeds captioned items into the vector search index. DB-driven and
    resumable; a captioned item is indexed exactly once."""

    def __init__(self, db: Database, milvus: MilvusManager, port: IndexPort):
        self.db = db
        self.milvus = milvus
        self.port = port

    async def run(self, retry_failed: bool = False):
        for category in self.port.chats:
            pending = await self.db.pending_embed(self.port.platform, category, retry_failed)
            if not pending:
                continue
            logger.info(f"Indexing {len(pending)} captioned {category} items")
            for item in pending:
                await self._index(item, category)

    async def _index(self, item: Item, loop_category: str):
        searchable = "\n\n".join(
            section for section in ((item.text or "").strip(), item.vlm_description) if section
        )
        if not searchable:
            await self.db.mark_embedded(item.item_id)  # captioned but nothing to index
            return
        try:
            await self.milvus.insert_embedding(
                category=self.port.embed_category(item, loop_category),
                item_id=item.item_id,
                embedding=embed_client.embed_document(searchable),
                text=searchable,
                media_type="media" if item.vlm_description else "text",
                caption=item.text,
                username=item.author_username,
            )
            await self.db.mark_embedded(item.item_id, item.vlm_description)
        except Exception as e:
            logger.error(f"Indexing failed for {item.item_id}: {e}")
            await self.db.mark_embed_failed(item.item_id, str(e))
