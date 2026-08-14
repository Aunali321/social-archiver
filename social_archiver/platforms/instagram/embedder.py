"""Instagram captioning. Posts have no reply graph, so each is described on its
own (album files individually), a few in parallel. The post caption is passed as
context so the description is grounded in what the post is about.

Captioning is its own stage: this produces the description and its trace and
marks caption_status; embedding it into the search index is the separate index
job. Every call is recorded as a trace, and refusals are kept apart from
technical failures so they can be retried on their own."""

import asyncio
import logging
from pathlib import Path

from social_archiver.core import config
from social_archiver.core.database import Database, Item
from social_archiver.core.jobs import ensure_media
from social_archiver.core.media_kind import guess_media_kind, guess_mime_type
from social_archiver.llm.vertex_client import VertexVLMClient
from social_archiver.llm.vlm_types import MediaResult, VlmStatus, VlmTrace
from social_archiver.platforms.instagram.port import PLATFORM, InstagramPort

logger = logging.getLogger(__name__)

_VLM_MEDIA_TYPE = {"photo": "image", "video": "video"}


class CaptionJob:
    """Captions archived Instagram posts. Purely DB-driven and resumable;
    produces captions and traces, never touches the search index."""

    def __init__(self, db: Database, vlm: VertexVLMClient, port: InstagramPort):
        self.db = db
        self.vlm = vlm
        self.port = port

    async def run(self, retry_failed: bool = False, retry_refused: bool = False, limit: int | None = None):
        remaining = limit
        for category in self.port.chats:
            if remaining is not None and remaining <= 0:
                break
            pending = await self.db.pending_caption(PLATFORM, category, retry_failed, retry_refused)
            if remaining is not None:
                pending = pending[:remaining]
                remaining -= len(pending)
            if not pending:
                continue

            logger.info(f"Captioning {len(pending)} {category} items, {config.EMBED_CONCURRENCY} at a time")
            semaphore = asyncio.Semaphore(config.EMBED_CONCURRENCY)
            async with asyncio.TaskGroup() as tasks:
                for item in pending:
                    tasks.create_task(self._caption_item(item, semaphore))

    async def _caption_item(self, item: Item, semaphore: asyncio.Semaphore):
        async with semaphore:
            try:
                paths = await ensure_media(self.db, self.port, item)
            except Exception as e:
                logger.error(f"Media unavailable for {item.item_id}: {e}")
                await self.db.mark_caption_failed(item.item_id, str(e))
                return

            results = [await self._describe(item, path, index) for index, path in enumerate(paths)]
            await self._settle(item, results)

    async def _describe(self, item: Item, path: Path, index: int) -> MediaResult:
        kind = guess_media_kind(path)
        result = await self.vlm.describe_media(path, _VLM_MEDIA_TYPE[kind], thread_context=(item.text or None))
        await self._record_trace(item, path, index, kind, result)
        return result

    async def _settle(self, item: Item, results: list[MediaResult]):
        """One status for the post from its media's outcomes: a technical failure
        anywhere retries the whole post; else a refusal marks it refused; else it
        is captioned on the descriptions that came back."""
        if any(r.status in (VlmStatus.FAILED, VlmStatus.TRUNCATED) for r in results):
            error = next(r.error for r in results if r.status in (VlmStatus.FAILED, VlmStatus.TRUNCATED))
            await self.db.mark_caption_failed(item.item_id, error or "vlm call did not complete")
        elif any(r.status is VlmStatus.REFUSED for r in results):
            reason = next(r.error or r.finish_reason for r in results if r.status is VlmStatus.REFUSED)
            await self.db.mark_caption_refused(item.item_id, reason or "refused")
        else:
            joined = "\n\n---\n\n".join(r.description for r in results if r.description)
            await self.db.mark_captioned(item.item_id, joined or None)
            logger.info(f"Captioned {item.item_id} (@{item.author_username})")

    async def _record_trace(self, item: Item, path: Path, index: int, kind: str, result: MediaResult):
        await self.db.insert_trace(
            VlmTrace(
                platform=PLATFORM,
                model=self.vlm.model,
                provider=self.vlm.provider,
                params=self.vlm.params,
                target_item_ids=[item.item_id],
                input=[
                    {"type": "text", "text": item.text or ""},
                    {
                        "type": "media",
                        "item_id": item.item_id,
                        "index": index,
                        "path": str(path),
                        "mime": guess_mime_type(path) or kind,
                    },
                ],
                reasoning=result.reasoning,
                output=result.description,
                finish_reason=result.finish_reason,
                status=result.status,
                usage=result.usage,
                error=result.error,
            )
        )
