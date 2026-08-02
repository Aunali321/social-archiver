import logging
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from social_archiver.core.database import Database, Item
from social_archiver.core.media_kind import guess_mime_type
from social_archiver.llm import embed_client
from social_archiver.llm.vertex_client import (
    MediaCaption,
    MediaPart,
    TextPart,
    ThreadCaptions,
    ThreadPart,
    VertexVLMClient,
)
from social_archiver.platforms.whatsapp.port import PLATFORM, WhatsAppPort

logger = logging.getLogger(__name__)

# One VLM call covers many items at once; these caps keep a large resumed backlog
# within the model's context window.
MAX_MEDIA_PER_CALL = 20
MAX_ITEMS_PER_CALL = 80


@dataclass(frozen=True, slots=True)
class DayGroup:
    """One chat-day, in chronological order. Only `target_ids` still need embeddings;
    the other members ride along as context for the VLM."""

    members: tuple[Item, ...]
    target_ids: frozenset[str]

    @property
    def planned_media(self) -> int:
        return sum(len(m.local_paths) for m in self.members if m.item_id in self.target_ids)


class EmbedJob:
    """Generates VLM descriptions and search embeddings for archived messages, a chat-day
    at a time so each message is read in its conversation. Media that is no longer on disk
    is media WhatsApp will never serve again, so such a message embeds on its text alone
    rather than failing."""

    def __init__(self, db: Database, vlm: VertexVLMClient, milvus, port: WhatsAppPort):
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
            logger.info(f"Embedding {len(pending)} {category} items across {len(groups)} chat-days")
            for batch in _pack(groups):
                await self._embed_batch(batch)

    async def _load_groups(self, pending: list[Item]) -> list[DayGroup]:
        target_ids = {item.item_id for item in pending}
        members = await self.db.thread_members(PLATFORM, {_thread_key(item) for item in pending})

        days: dict[str, list[Item]] = defaultdict(list)
        for member in members:
            days[_thread_key(member)].append(member)

        return [
            DayGroup(
                members=tuple(day),
                target_ids=frozenset(target_ids.intersection(m.item_id for m in day)),
            )
            for day in days.values()
        ]

    async def _embed_batch(self, batch: list[DayGroup]):
        members = [member for group in batch for member in group.members]
        targets = set().union(*(group.target_ids for group in batch))
        media_paths = {member.item_id: _existing_media(member) for member in members}

        descriptions: dict[str, str] = {}
        if any(media_paths[target] for target in targets):
            parts = _interleave(members, media_paths)
            logger.info(f"VLM call: {len(members)} items, {sum(map(len, media_paths.values()))} media items")

            captions = await self.vlm.describe_thread(parts)
            if captions is None:
                for target in targets:
                    await self.db.mark_embed_failed(target, "VLM thread description failed")
                return
            descriptions = _merge_captions(captions)

        for member in members:
            if member.item_id in targets:
                description = descriptions.get(member.item_id)
                await self._index(member, description)
                await self.db.mark_embedded(member.item_id, description)

    async def _index(self, item: Item, description: str | None):
        sections = [section for section in ((item.text or "").strip(), description) if section]
        if not sections:
            return  # nothing searchable; the item stays archived either way

        searchable = "\n\n".join(sections)
        await self.milvus.insert_embedding(
            category=item.category,
            item_id=item.item_id,
            embedding=embed_client.embed_document(searchable),
            text=searchable,
            media_type="media" if description else "text",
            caption=item.text,
            username=item.author_username,
        )


def _thread_key(item: Item) -> str:
    return item.thread_root_id or item.item_id


def _existing_media(item: Item) -> list[Path]:
    """Only what is still on disk. There are no URLs to restore from — WhatsApp media lives
    encrypted on a CDN that expires it — so a missing file is a recorded loss, not an error."""
    return [path for path in item.local_paths if path.exists()]


def _pack(groups: list[DayGroup]) -> Iterator[list[DayGroup]]:
    """Pack whole chat-days into VLM calls without splitting any day."""
    batch: list[DayGroup] = []
    media = items = 0

    for group in groups:
        overflows = media + group.planned_media > MAX_MEDIA_PER_CALL or items + len(group.members) > MAX_ITEMS_PER_CALL
        if batch and overflows:
            yield batch
            batch, media, items = [], 0, 0
        batch.append(group)
        media += group.planned_media
        items += len(group.members)

    if batch:
        yield batch


def _interleave(members: list[Item], media_paths: dict[str, list[Path]]) -> list[ThreadPart]:
    parts: list[ThreadPart] = []
    for member in members:
        parts.append(TextPart(_label(member)))
        for path in media_paths[member.item_id]:
            if mime_type := guess_mime_type(path):
                parts.append(MediaPart(path, mime_type))
    return parts


def _label(member: Item) -> str:
    # The [tweet_id:...] token is the id label the shared VLM client echoes back.
    label = f"[tweet_id:{member.item_id}] {member.author_username}"
    if member.chat_name and member.chat_name != member.author_username:
        label += f" in {member.chat_name}"
    if text := (member.text or "").strip():
        label += f": {text}"
    return label


def _merge_captions(captions: ThreadCaptions) -> dict[str, str]:
    """Fold per-media captions into one description per item."""
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
