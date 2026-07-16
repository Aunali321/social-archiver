import logging
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from social_archiver.core.database import Database, Item
from social_archiver.core.jobs import ensure_media
from social_archiver.core.media_kind import guess_mime_type
from social_archiver.core.milvus_manager import MilvusManager
from social_archiver.llm import local_embedder
from social_archiver.llm.vertex_client import MediaCaption, MediaPart, TextPart, ThreadCaptions, ThreadPart, VertexVLMClient
from social_archiver.platforms.twitter.port import PLATFORM, SEED_ORIGINS, TwitterPort

logger = logging.getLogger(__name__)

# One VLM call covers many tweets at once; these caps keep a large resumed
# backlog within the model's context window.
MAX_MEDIA_PER_CALL = 20
MAX_TWEETS_PER_CALL = 80


@dataclass(frozen=True, slots=True)
class ThreadGroup:
    """One thread, in chronological order. Only `target_ids` still need
    embeddings; the other members ride along as context for the VLM."""

    members: tuple[Item, ...]
    target_ids: frozenset[str]

    @property
    def planned_media(self) -> int:
        return sum(m.media_count for m in self.members if m.item_id in self.target_ids)


class EmbedJob:
    """Generates VLM descriptions and search embeddings for archived tweets.

    Threads are reconstructed from the database, so a tweet embedded weeks
    after archival still gets its full conversation as context, and an
    interrupted run resumes with whatever is still pending."""

    def __init__(self, db: Database, vlm: VertexVLMClient, milvus: MilvusManager, port: TwitterPort):
        self.db = db
        self.vlm = vlm
        self.milvus = milvus
        self.port = port

    async def run(self, retry_failed: bool = False):
        for category in self.port.chats:
            pending = await self.db.pending_embed(PLATFORM, category, retry_failed)
            if not pending:
                continue

            groups = await self._load_groups(pending)
            logger.info(f"Embedding {len(pending)} {category} tweets across {len(groups)} threads")
            for batch in _pack(groups):
                await self._embed_batch(batch, category)

    async def _load_groups(self, pending: list[Item]) -> list[ThreadGroup]:
        target_ids = {item.item_id for item in pending}
        members = await self.db.thread_members(PLATFORM, {_thread_key(item) for item in pending})

        threads: dict[str, list[Item]] = defaultdict(list)
        for member in members:
            threads[_thread_key(member)].append(member)

        return [
            ThreadGroup(members=tuple(thread), target_ids=frozenset(target_ids.intersection(m.item_id for m in thread)))
            for thread in threads.values()
        ]

    async def _embed_batch(self, batch: list[ThreadGroup], category: str):
        members = [member for group in batch for member in group.members]
        targets = set().union(*(group.target_ids for group in batch))
        media_paths, targets = await self._collect_media(members, targets)

        descriptions: dict[str, str] = {}
        if any(media_paths[target] for target in targets):
            parts = _interleave(members, media_paths)
            logger.info(f"VLM call: {len(members)} tweets, {sum(map(len, media_paths.values()))} media items")

            captions = await self.vlm.describe_thread(parts)
            if captions is None:
                for target in targets:
                    await self.db.mark_embed_failed(target, "VLM thread description failed")
                return
            descriptions = _merge_captions(captions)

        for member in members:
            if member.item_id in targets:
                description = descriptions.get(member.item_id)
                await self._index(member, description, category)
                await self.db.mark_embedded(member.item_id, description)

    async def _collect_media(self, members: list[Item], targets: set[str]) -> tuple[dict[str, list[Path]], set[str]]:
        """Media files for the target tweets, restored from their URLs when
        missing. A tweet whose media can't be restored is marked failed and
        dropped; the rest of the batch proceeds."""
        media_paths: dict[str, list[Path]] = {member.item_id: [] for member in members}
        usable = set(targets)

        for member in members:
            if member.item_id not in targets or not member.has_media:
                continue
            try:
                media_paths[member.item_id] = await ensure_media(self.db, self.port, member)
            except Exception as e:
                logger.error(f"Media unavailable for {member.item_id}: {e}")
                await self.db.mark_embed_failed(member.item_id, str(e))
                usable.discard(member.item_id)

        return media_paths, usable

    async def _index(self, item: Item, description: str | None, category: str):
        sections = [section for section in ((item.text or "").strip(), description) if section]
        if not sections:
            return  # nothing searchable; the tweet stays archived either way

        searchable = "\n\n".join(sections)
        await self.milvus.insert_embedding(
            category=category,
            item_id=item.item_id,
            embedding=local_embedder.embed_document(searchable),
            text=searchable,
            media_type="media" if description else "text",
            caption=item.text,
            username=item.author_username,
        )


def _thread_key(item: Item) -> str:
    return item.thread_root_id or item.item_id


def _pack(groups: list[ThreadGroup]) -> Iterator[list[ThreadGroup]]:
    """Pack whole threads into VLM calls without splitting any thread."""
    batch: list[ThreadGroup] = []
    media = tweets = 0

    for group in groups:
        overflows = media + group.planned_media > MAX_MEDIA_PER_CALL or tweets + len(group.members) > MAX_TWEETS_PER_CALL
        if batch and overflows:
            yield batch
            batch, media, tweets = [], 0, 0
        batch.append(group)
        media += group.planned_media
        tweets += len(group.members)

    if batch:
        yield batch


def _interleave(members: list[Item], media_paths: dict[str, list[Path]]) -> list[ThreadPart]:
    by_id = {member.item_id: member for member in members}

    parts: list[ThreadPart] = []
    for member in members:
        parts.append(TextPart(_label(member, by_id)))
        for path in media_paths[member.item_id]:
            if mime_type := guess_mime_type(path):
                parts.append(MediaPart(path, mime_type))
    return parts


def _label(member: Item, by_id: dict[str, Item]) -> str:
    label = f"[tweet_id:{member.item_id}]"
    if member.origin and member.origin not in SEED_ORIGINS:
        label += f" [{member.origin}]"
    label += f" @{member.author_username}"
    if text := (member.text or "").strip():
        label += f": {text}"

    quoted = by_id.get(member.quoted_tweet_id) if member.quoted_tweet_id else None
    if quoted and (quoted_text := (quoted.text or "").strip()):
        label += f"\n  ↳ Quotes @{quoted.author_username}: {quoted_text}"

    return label


def _merge_captions(captions: ThreadCaptions) -> dict[str, str]:
    """Fold per-media captions into one description per tweet."""
    merged: dict[str, str] = {}
    for caption in captions.captions:
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
