"""Shared VLM captioning engine for thread-shaped platforms.

Twitter, Reddit and WhatsApp all caption media in the context of the
conversation it sits in: the thread is reconstructed from the archive, its text
interleaved with the media, and every item described in one call. What differs
is only how a post is labeled, how its media is restored, and what extra
context a platform's graph adds — the `CaptionPort` supplies those; the engine
is one.

Captioning is its own stage, independent of building the search index: this job
produces the caption and its trace and marks `caption_status`; embedding that
caption into the vector index is a later, separate job. So captioning needs no
embedding server or GPU, and the caption is full-text searchable through the FTS
index the moment it lands.

Threads are reconstructed from the database, so an item captioned long after
archival still gets its full conversation as context, and an interrupted run
resumes with whatever is still pending. A run works through the backlog a batch
of conversations at a time, so what it holds is bounded by the batch rather than
by the size of the backlog, which reaches millions of posts. The conversation is
passed whole — there is no per-thread cap on context, only a safety ceiling on
pathological threads. A single item's media is never split across calls, so each
target is captioned in exactly one call and its status is unambiguous.

Every call is recorded as a trace (input with media as path references, the
model's reasoning and raw output, usage, status) before the item statuses are
written, so the run is auditable, refusals are retryable on their own, and the
outputs form a distillation dataset.
"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from social_archiver.core import config
from social_archiver.core.database import Database, Item
from social_archiver.core.media_kind import guess_mime_type
from social_archiver.llm.vertex_client import VertexVLMClient
from social_archiver.llm.vlm_types import (
    MediaCaption,
    MediaPart,
    TextPart,
    ThreadCaptions,
    ThreadPart,
    ThreadResult,
    VlmStatus,
    VlmTrace,
    serialize_parts,
)

logger = logging.getLogger(__name__)


class CaptionPort(Protocol):
    """What the engine needs from a platform to caption its threads."""

    platform: str
    chats: dict[str, int]

    def embed_label(self, item: Item, context: dict[str, Item]) -> str:
        """The `[tweet_id:...]`-prefixed line for this item. `context` maps id to
        item for the whole call, so a platform can render an inline quote."""

    def embed_thread_key(self, item: Item) -> str:
        """The id that groups items into one conversation."""

    async def embed_collect_media(self, db: Database, item: Item) -> list[Path]:
        """The item's media files. Raises if a restorable platform cannot
        restore them (a retryable failure); returns [] if the media is simply
        gone, in which case the item is captioned on its text alone."""

    async def embed_extra_context(self, db: Database, members: list[Item]) -> list[Item]:
        """Items outside the threads that their labels reference (e.g. quoted
        posts in other conversations). Context only, never captioned here."""


@dataclass(frozen=True, slots=True)
class _Conversation:
    members: tuple[Item, ...]
    target_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class _Call:
    """One VLM call: the ordered posts to render as context, the id->item map its
    labels resolve against, and the target items this call captions."""

    ordered: tuple[Item, ...]
    context_map: dict[str, Item]
    targets: tuple[Item, ...]


class CaptionJob:
    """Captions archived media a conversation at a time. Purely DB-driven and
    safe to resume; produces captions and traces, never touches the search index."""

    def __init__(self, db: Database, vlm: VertexVLMClient, port: CaptionPort):
        self.db = db
        self.vlm = vlm
        self.port = port

    async def run(self, retry_failed: bool = False, retry_refused: bool = False, limit: int | None = None):
        remaining = limit
        for category in self.port.chats:
            if remaining is not None and remaining <= 0:
                break
            roots = await self.db.pending_caption_roots(self.port.platform, category, retry_failed, retry_refused)
            if not roots:
                continue
            logger.info(
                f"Captioning {category}: {len(roots)} conversations pending, "
                f"{config.CAPTION_BATCH_CONVERSATIONS} conversations per batch"
            )
            for start in range(0, len(roots), config.CAPTION_BATCH_CONVERSATIONS):
                if remaining is not None and remaining <= 0:
                    break
                batch = roots[start : start + config.CAPTION_BATCH_CONVERSATIONS]
                captioned = await self._run_batch(category, batch, retry_failed, retry_refused, remaining)
                if remaining is not None:
                    remaining -= captioned

    async def _run_batch(
        self, category: str, roots: list[str], retry_failed: bool, retry_refused: bool, cap: int | None
    ) -> int:
        """One batch of conversations, loaded and released together. Batching is
        what bounds a run's memory: the pending set and every thread member of a
        platform with a large backlog do not fit at once."""
        pending = await self.db.pending_caption(self.port.platform, category, roots, retry_failed, retry_refused)
        if cap is not None:
            pending = pending[:cap]
        if not pending:
            return 0

        conversations, context_map = await self._load(pending)

        for conversation in conversations:
            for member in conversation.members:
                if member.item_id in conversation.target_ids and not member.media_count:
                    await self.db.mark_captioned(member.item_id, None)

        calls = [call for conversation in conversations for call in self._plan_calls(conversation, context_map)]
        logger.info(
            f"Captioning {len(pending)} {category} items across {len(conversations)} conversations "
            f"in {len(calls)} VLM calls, {config.EMBED_CONCURRENCY} at a time"
        )

        semaphore = asyncio.Semaphore(config.EMBED_CONCURRENCY)
        async with asyncio.TaskGroup() as tasks:
            for call in calls:
                tasks.create_task(self._run_call(call, semaphore))
        return len(pending)

    async def _load(self, pending: list[Item]) -> tuple[list[_Conversation], dict[str, Item]]:
        target_ids = {item.item_id for item in pending}
        members = await self.db.thread_members(
            self.port.platform, {self.port.embed_thread_key(item) for item in pending}
        )
        member_ids = {member.item_id for member in members}
        extra = await self.port.embed_extra_context(self.db, members)
        # One lookup for every loaded item, so a label can resolve a quoted post
        # whether it sits in this thread, another pending thread, or was fetched
        # only as context.
        context_map = {member.item_id: member for member in members}
        context_map.update({item.item_id: item for item in extra if item.item_id not in member_ids})

        threads: dict[str, list[Item]] = defaultdict(list)
        for member in members:
            threads[self.port.embed_thread_key(member)].append(member)

        conversations = []
        for thread in threads.values():
            capped = _cap_context(thread, target_ids)
            # Targets are drawn from the capped members, so a target the safety
            # cap dropped is never left thinking it will be captioned this run.
            conversations.append(
                _Conversation(
                    members=tuple(capped),
                    target_ids=frozenset(target_ids.intersection(m.item_id for m in capped)),
                )
            )
        return conversations, context_map

    def _plan_calls(self, conversation: _Conversation, context_map: dict[str, Item]) -> list[_Call]:
        """Split media-bearing targets into calls of at most
        EMBED_MAX_MEDIA_PER_CALL media, never splitting one item across calls.
        Every call renders the whole conversation as context."""
        chunks: list[list[Item]] = []
        current: list[Item] = []
        media_in_current = 0
        for member in conversation.members:
            if member.item_id not in conversation.target_ids or not member.media_count:
                continue
            if current and media_in_current + member.media_count > config.EMBED_MAX_MEDIA_PER_CALL:
                chunks.append(current)
                current, media_in_current = [], 0
            current.append(member)
            media_in_current += member.media_count
        if current:
            chunks.append(current)

        return [_Call(conversation.members, context_map, tuple(chunk)) for chunk in chunks]

    async def _run_call(self, call: _Call, semaphore: asyncio.Semaphore):
        async with semaphore:
            try:
                await self._caption_call(call)
            except Exception as e:
                # One call's unexpected failure marks its own targets failed
                # (retryable) rather than cancelling the whole parallel batch, so
                # a run always makes progress and resumes cleanly.
                logger.error(f"Caption call crashed: {e}", exc_info=True)
                for target in call.targets:
                    await self.db.mark_caption_failed(target.item_id, f"call crashed: {e}")

    async def _caption_call(self, call: _Call):
        media = await self._collect_media(call.targets)
        captionable = [target for target in call.targets if media.get(target.item_id)]
        if not captionable:
            return  # every target was text-only or failed; each already handled

        parts = _interleave(call.ordered, media, call.context_map, self.port)
        logger.info(
            f"VLM call: {len(call.ordered)} posts context, "
            f"{sum(len(m) for m in media.values())} media, {len(captionable)} targets"
        )
        result = await self.vlm.describe_thread(parts)
        await self._record_trace(parts, [t.item_id for t in captionable], result)
        await self._apply(result, captionable)

    async def _collect_media(self, targets: tuple[Item, ...]) -> dict[str, list[tuple[int, Path, str]]]:
        """Restore each target's media. A restore that raises is a retryable
        failure (marked failed). Media that is simply gone, unsupported, or too
        large to send inline leaves the item captioned on its text alone."""
        media: dict[str, list[tuple[int, Path, str]]] = {}
        for target in targets:
            try:
                paths = await self.port.embed_collect_media(self.db, target)
            except Exception as e:
                logger.error(f"Media unavailable for {target.item_id}: {e}")
                await self.db.mark_caption_failed(target.item_id, str(e))
                continue

            usable: list[tuple[int, Path, str]] = []
            for index, path in enumerate(paths):
                mime = guess_mime_type(path)
                if not mime:
                    continue
                if path.stat().st_size > config.VLM_MAX_MEDIA_BYTES:
                    logger.warning(f"Media too large to caption inline, captioning on text: {path.name}")
                    continue
                usable.append((index, path, mime))

            if usable:
                media[target.item_id] = usable
            else:
                await self.db.mark_captioned(target.item_id, None)
        return media

    async def _apply(self, result: ThreadResult, targets: list[Item]):
        if result.status is VlmStatus.REFUSED:
            reason = result.error or result.finish_reason or "refused"
            for target in targets:
                await self.db.mark_caption_refused(target.item_id, reason)
            logger.warning(f"VLM refused {len(targets)} targets ({result.finish_reason})")
            return
        if result.status in (VlmStatus.FAILED, VlmStatus.TRUNCATED):
            for target in targets:
                await self.db.mark_caption_failed(target.item_id, result.error or "vlm call did not complete")
            logger.error(f"VLM {result.status} for {len(targets)} targets: {result.error}")
            return

        descriptions = _merge_captions(result.captions)
        for target in targets:
            await self.db.mark_captioned(target.item_id, descriptions.get(target.item_id), self.vlm.model)

    async def _record_trace(self, parts: list[ThreadPart], target_ids: list[str], result: ThreadResult):
        await self.db.insert_trace(
            VlmTrace(
                platform=self.port.platform,
                model=self.vlm.model,
                provider=self.vlm.provider,
                params=self.vlm.params,
                target_item_ids=target_ids,
                input=serialize_parts(parts),
                reasoning=result.reasoning,
                reasoning_summary=result.reasoning_summary,
                output=result.raw_output,
                finish_reason=result.finish_reason,
                status=result.status,
                usage=result.usage,
                error=result.error,
            )
        )


def _cap_context(members: list[Item], target_ids: set[str]) -> list[Item]:
    """Keep the whole thread as context unless it is pathologically large, in
    which case keep the window of members around the targets."""
    if len(members) <= config.VLM_MAX_CONTEXT_POSTS:
        return members
    positions = [i for i, m in enumerate(members) if m.item_id in target_ids]
    if not positions:
        return members[: config.VLM_MAX_CONTEXT_POSTS]
    span = config.VLM_MAX_CONTEXT_POSTS
    pad = max(0, (span - (max(positions) - min(positions) + 1)) // 2)
    start = max(0, min(positions) - pad)
    return members[start : start + span]


def _interleave(
    ordered: tuple[Item, ...],
    media: dict[str, list[tuple[int, Path, str]]],
    context_map: dict[str, Item],
    port: CaptionPort,
) -> list[ThreadPart]:
    parts: list[ThreadPart] = []
    for member in ordered:
        parts.append(TextPart(port.embed_label(member, context_map)))
        for index, path, mime in media.get(member.item_id, ()):
            parts.append(MediaPart(path=path, mime_type=mime, item_id=member.item_id, media_index=index))
    return parts


def _merge_captions(captions: ThreadCaptions | None) -> dict[str, str]:
    """Fold per-media captions into one description per item."""
    merged: dict[str, str] = {}
    for caption in captions.captions if captions else ():
        block = _caption_block(caption)
        if existing := merged.get(caption.tweet_id):
            block = f"{existing}\n\n---\n\n{block}"
        merged[caption.tweet_id] = block
    return merged


def _caption_block(caption: MediaCaption) -> str:
    sections = [caption.visual_description]
    if caption.visible_text:
        sections.append(f"Visible text: {caption.visible_text}")
    if caption.speech_transcript:
        sections.append(f"Speech: {caption.speech_transcript}")
    if caption.audio_description:
        sections.append(f"Audio: {caption.audio_description}")
    return "\n\n".join(sections)
