"""The /api content endpoints: what the archive holds, served read-only for the viewer.

Everything here goes through read.ArchiveReader against `mode=ro` connections — the workers
in this same process stay the only writers. The one exception is media recovery, which
reuses the archival pipeline's own ensure_media on an explicit request, never on browse.
"""

import importlib
from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from social_archiver.core import config
from social_archiver.core.config import PLATFORMS
from social_archiver.core.database import Database, Item
from social_archiver.core.jobs import ensure_media
from social_archiver.read import conversation, semantic
from social_archiver.read.models import ItemFilters, is_seed
from social_archiver.read.store import ArchiveReader

router = APIRouter()
reader = ArchiveReader(config.DATA_DIR)


class MediaRef(BaseModel):
    index: int
    type: str | None
    available: bool  # on disk right now; uploads may have been cleaned up


class ItemContext(BaseModel):
    """The graph neighbours a feed needs to make an item legible in place: the tweet this
    replies to, the tweet it quotes or retweets, the submission a reddit comment sits under,
    the chat message a WhatsApp reply points at. Context items carry no context of their own."""

    parent: "ItemOut | None" = None
    quoted: "ItemOut | None" = None
    retweeted: "ItemOut | None" = None
    submission: "ItemOut | None" = None


class ItemOut(BaseModel):
    item_id: str
    platform: str
    category: str
    author_username: str
    author_id: str | None
    post_url: str
    text: str | None
    title: str | None  # reddit posts store "**title**\n\nbody" in text; served split
    is_article: bool
    created_at: datetime | None
    chat_name: str | None
    subreddit: str | None
    link_url: str | None
    collection_name: str | None
    shared_by_username: str | None
    product_type: str | None
    conversation_id: str | None
    in_reply_to_status_id: str | None
    quoted_tweet_id: str | None
    retweeted_tweet_id: str | None
    is_retweet: bool
    origin: str | None
    discovered_via_item_id: str | None
    thread_root_id: str | None
    source_target: str | None
    reply_count: int | None
    retweet_count: int | None
    like_count: int | None
    quote_count: int | None
    bookmark_count: int | None
    view_count: int | None
    archive_status: str
    upload_status: str
    embed_status: str
    archive_error: str | None
    vlm_description: str | None
    telegram_message_ids: list[int]
    media: list[MediaRef]
    context: ItemContext | None = None
    # Worth offering a thread expansion: the archive holds (or likely holds) siblings
    in_thread: bool = False
    # The user acted on this item (liked/saved/...); False for expander-adopted context
    is_seed: bool = True


class PageOut(BaseModel):
    items: list[ItemOut]
    next_cursor: str | None


class SearchHitOut(BaseModel):
    item: ItemOut
    snippet: str | None
    score: float | None = None


class SearchOut(BaseModel):
    mode: str
    hits: list[SearchHitOut]
    semantic_platforms: list[str]  # where semantic search is answerable, for the UI toggle


class ItemDetailOut(BaseModel):
    item: ItemOut
    categories: list[str]
    collections: list[str]
    parent: ItemOut | None
    quoted: ItemOut | None
    retweeted: ItemOut | None
    discovered_via: ItemOut | None
    replies: list[ItemOut]


class ChatOut(BaseModel):
    chat_id: str
    name: str | None
    category: str
    message_count: int
    last_at: datetime | None
    last_author: str | None
    last_text: str | None


class AuthorOut(BaseModel):
    author: str
    items: int


class FacetsOut(BaseModel):
    categories: dict[str, int]
    origins: dict[str, int]
    subreddits: dict[str, int]
    collections: dict[str, int]


class ArchiveStatsOut(BaseModel):
    platform: str
    total: int
    categories: dict[str, int]
    archive: dict[str, int]
    upload: dict[str, int]
    embed: dict[str, int]
    with_media: int
    with_local_media: int
    authors: int
    oldest: datetime | None
    newest: datetime | None
    by_month: dict[str, int]


_COMPUTED_FIELDS = {"media", "context", "in_thread", "title", "is_seed"}


def _split_reddit_title(item: Item) -> tuple[str | None, str | None]:
    """Inverse of the archiver's "**{title}**\n\n{selftext}" folding, for posts only."""
    if item.platform != "reddit" or not item.item_id.startswith("t3_") or not item.text:
        return None, item.text
    head, _, body = item.text.partition("\n\n")
    if head.startswith("**") and head.endswith("**") and len(head) > 4:
        return head[2:-2], body or None
    return None, item.text


def item_out(item: Item) -> ItemOut:
    title, text = _split_reddit_title(item)
    media = [
        MediaRef(
            index=index,
            type=item.media_types[index] if index < len(item.media_types) else None,
            available=index < len(item.local_paths) and item.local_paths[index].exists(),
        )
        for index in range(max(item.media_count, len(item.local_paths)))
    ]
    in_thread = bool(
        item.platform == "twitter"
        and item.thread_root_id
        and (item.has_self_replies or item.thread_position is not None)
    )
    out = ItemOut(
        **{name: getattr(item, name) for name in ItemOut.model_fields if name not in _COMPUTED_FIELDS},
        title=title,
        media=media,
        in_thread=in_thread,
        is_seed=is_seed(item),
    )
    out.text = text
    return out


def _context_refs(item: Item) -> dict[str, str]:
    """What an item points at that a reader needs in view, by context slot. A reddit comment's
    submission rides on thread_root_id; the reply/quote/retweet slots are explicit columns."""
    refs = {
        "parent": item.in_reply_to_status_id,
        "quoted": item.quoted_tweet_id,
        "retweeted": item.retweeted_tweet_id,
    }
    if item.platform == "reddit" and item.item_id.startswith("t1_"):
        refs["submission"] = item.thread_root_id
    return {slot: ref for slot, ref in refs.items() if ref}


async def with_context(items: list[Item]) -> list[ItemOut]:
    """Feed items with their graph neighbours attached: a bare "@x thanks!" is unreadable
    without the tweet it answers. One batched lookup per platform per page."""
    wanted: dict[str, set[str]] = {}
    for item in items:
        for ref in _context_refs(item).values():
            wanted.setdefault(item.platform, set()).add(ref)
    neighbours = {platform: await reader.get_many(platform, refs) for platform, refs in wanted.items()}

    entries = []
    for item in items:
        entry = item_out(item)
        held = neighbours.get(item.platform, {})
        found = {slot: item_out(held[ref]) for slot, ref in _context_refs(item).items() if ref in held}
        if found:
            entry.context = ItemContext(**found)
        entries.append(entry)
    return entries


def _filters(
    platforms: str | None,
    category: str | None,
    author: str | None,
    subreddit: str | None,
    chat: str | None,
    collection: str | None,
    origin: str | None,
    archive_status: str | None,
    source_target: str | None,
    has_media: bool | None,
    seeds_only: bool,
    date_from: datetime | None,
    date_to: datetime | None,
) -> ItemFilters:
    named = tuple(p.strip() for p in platforms.split(",") if p.strip()) if platforms else ()
    if unknown := set(named) - set(PLATFORMS):
        raise HTTPException(400, f"unknown platform {', '.join(sorted(unknown))}")
    return ItemFilters(
        platforms=named,
        category=category,
        author=author,
        subreddit=subreddit,
        chat=chat,
        collection=collection,
        origin=origin,
        archive_status=archive_status,
        source_target=source_target,
        has_media=has_media,
        seeds_only=seeds_only,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/api/items", response_model=PageOut)
async def list_items(
    platforms: str | None = None,
    category: str | None = None,
    author: str | None = None,
    subreddit: str | None = None,
    chat: str | None = None,
    collection: str | None = None,
    origin: str | None = None,
    archive_status: str | None = None,
    source_target: str | None = None,
    has_media: bool | None = None,
    seeds_only: bool = False,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=100),
) -> PageOut:
    filters = _filters(
        platforms, category, author, subreddit, chat, collection, origin,
        archive_status, source_target, has_media, seeds_only, date_from, date_to,
    )  # fmt: skip
    try:
        page = await reader.list_items(filters, cursor=cursor, limit=limit)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return PageOut(items=await with_context(page.items), next_cursor=page.next_cursor)


@router.get("/api/search", response_model=SearchOut)
async def search(
    q: str = Query(min_length=1),
    mode: str = Query("text", pattern="^(text|semantic)$"),
    platforms: str | None = None,
    category: str | None = None,
    author: str | None = None,
    subreddit: str | None = None,
    chat: str | None = None,
    has_media: bool | None = None,
    seeds_only: bool = False,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> SearchOut:
    filters = _filters(
        platforms, category, author, subreddit, chat, None, None, None, None, has_media, seeds_only, date_from, date_to
    )
    semantic_platforms = semantic.available()
    if mode == "semantic":
        if not semantic_platforms:
            raise HTTPException(
                400, "semantic search is not configured; EMBEDDING_ENABLED is off or nothing is embedded"
            )
        hits = await semantic.search(q, filters.platforms, limit=limit)
        found = [(hit, item) for hit in hits if (item := await reader.get(hit.platform, hit.item_id))]
        entries = await with_context([item for _, item in found])
        return SearchOut(
            mode="semantic",
            hits=[
                SearchHitOut(item=entry, snippet=hit.caption, score=hit.score)
                for (hit, _), entry in zip(found, entries, strict=True)
            ],
            semantic_platforms=semantic_platforms,
        )

    hits = await reader.search(q, filters, limit=limit, offset=offset)
    entries = await with_context([hit.item for hit in hits])
    return SearchOut(
        mode="text",
        hits=[SearchHitOut(item=entry, snippet=hit.snippet) for hit, entry in zip(hits, entries, strict=True)],
        semantic_platforms=semantic_platforms,
    )


@router.get("/api/items/{platform}/{item_id}", response_model=ItemDetailOut)
async def item_detail(platform: str, item_id: str) -> ItemDetailOut:
    item = await reader.get(platform, item_id)
    if item is None:
        raise HTTPException(404, f"no {platform} item {item_id}")
    related = await reader.related(platform, item)
    categories, collections = await reader.memberships(platform, item_id)
    replies = await with_context(await reader.replies(platform, item_id))
    return ItemDetailOut(
        item=item_out(item),
        categories=categories,
        collections=collections,
        parent=item_out(related["parent"]) if related["parent"] else None,
        quoted=item_out(related["quoted"]) if related["quoted"] else None,
        retweeted=item_out(related["retweeted"]) if related["retweeted"] else None,
        discovered_via=item_out(related["discovered_via"]) if related["discovered_via"] else None,
        replies=replies,
    )


@router.get("/api/threads/{platform}/{root_id}", response_model=list[ItemOut])
async def thread(platform: str, root_id: str) -> list[ItemOut]:
    members = await reader.thread(platform, root_id)
    if not members:
        raise HTTPException(404, f"no {platform} thread {root_id}")
    return [item_out(i) for i in members]


class ConversationNodeOut(BaseModel):
    item: ItemOut
    replies: list["ConversationNodeOut"]


class ConversationOut(BaseModel):
    """A discussion assembled around one item: ancestors above, the reply tree below.
    Seed flags let any consumer separate what the user liked from adopted context."""

    focus: ItemOut
    ancestors: list[ItemOut]  # root first, immediate parent last
    missing_parent: bool  # the chain continues above what the archive holds
    replies: list[ConversationNodeOut]


def _node_out(node: conversation.ConversationNode) -> ConversationNodeOut:
    return ConversationNodeOut(item=item_out(node.item), replies=[_node_out(r) for r in node.replies])


@router.get("/api/conversation/{platform}/{item_id}", response_model=ConversationOut)
async def get_conversation(platform: str, item_id: str) -> ConversationOut:
    if platform not in conversation.CONVERSATION_PLATFORMS:
        raise HTTPException(400, f"{platform} has no conversation trees; use the chat or item view")
    tree = await conversation.load(reader, platform, item_id)
    if tree is None:
        raise HTTPException(404, f"no {platform} item {item_id}")
    return ConversationOut(
        focus=item_out(tree.focus),
        ancestors=[item_out(a) for a in tree.ancestors],
        missing_parent=tree.missing_parent,
        replies=[_node_out(r) for r in tree.replies],
    )


@router.get("/api/chats/{platform}", response_model=list[ChatOut])
async def chats(platform: str) -> list[ChatOut]:
    return [ChatOut(**asdict(c)) for c in await reader.chats(platform)]


@router.get("/api/authors/{platform}", response_model=list[AuthorOut])
async def authors(platform: str, prefix: str = "", limit: int = Query(20, ge=1, le=100)) -> list[AuthorOut]:
    return [AuthorOut(author=a.author, items=a.items) for a in await reader.authors(platform, prefix, limit)]


@router.get("/api/facets", response_model=dict[str, FacetsOut])
async def facets() -> dict[str, FacetsOut]:
    return {p: FacetsOut(**asdict(await reader.facets(p))) for p in await reader.present()}


@router.get("/api/archive/stats", response_model=list[ArchiveStatsOut])
async def archive_stats() -> list[ArchiveStatsOut]:
    return [ArchiveStatsOut(**asdict(await reader.stats(p))) for p in await reader.present()]


@router.get("/api/media/{platform}/{item_id}/{index}")
async def media(platform: str, item_id: str, index: int) -> FileResponse:
    item = await reader.get(platform, item_id)
    if item is None:
        raise HTTPException(404, f"no {platform} item {item_id}")
    if index >= len(item.local_paths):
        raise HTTPException(404, "not on disk; media may have been cleaned up after upload")
    path = item.local_paths[index]
    if not path.exists():
        raise HTTPException(404, "not on disk; media may have been cleaned up after upload")
    return FileResponse(path)


@router.post("/api/items/{platform}/{item_id}/recover-media", response_model=ItemOut)
async def recover_media(platform: str, item_id: str) -> ItemOut:
    """Re-download an item's media from its stored URLs, through the same ensure_media the
    upload pipeline uses. Explicitly user-triggered: browsing must never hit the platforms."""
    item = await reader.get(platform, item_id)
    if item is None:
        raise HTTPException(404, f"no {platform} item {item_id}")
    service = importlib.import_module(f"social_archiver.platforms.{platform}.service")
    db = Database(config.DATA_DIR / f"{platform}.db")
    await db.connect()
    try:
        await ensure_media(db, service.PORT, item)
        refreshed = await db.get(item_id)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    finally:
        await db.close()
    return item_out(refreshed)


@router.get("/api/platforms")
async def platforms_present() -> dict[str, list[str] | dict]:
    """What exists to browse, for the UI's navigation and filter setup."""
    return {"platforms": await reader.present(), "semantic": semantic.available()}
