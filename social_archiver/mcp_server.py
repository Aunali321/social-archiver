"""MCP server over the read layer, so an assistant can query the archive.

Read-only by construction: every tool goes through ArchiveReader's mode=ro connections.
Served over streamable HTTP at /mcp by the web process (see social_archiver.api), behind the
same owner sign-in as the UI, so any MCP host that speaks OAuth can connect by URL alone.
"""

from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.auth.provider import OAuthAuthorizationServerProvider
from mcp.server.auth.settings import AuthSettings
from mcp.types import ToolAnnotations

from social_archiver.core import config
from social_archiver.core.database import Item
from social_archiver.core.media_links import links
from social_archiver.read import ArchiveReader, ItemFilters
from social_archiver.read import conversation as conversations
from social_archiver.read import semantic as semantic_search
from social_archiver.read.models import SearchSort, bracketed, is_seed

reader = ArchiveReader(config.DATA_DIR)

_READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


def _item(item: Item) -> dict[str, Any]:
    """Compact and text-first: what a model needs to reason about a post, not pipeline state."""
    out: dict[str, Any] = {
        "platform": item.platform,
        "item_id": item.item_id,
        "author": item.author_username,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "category": item.category,
        "url": item.post_url,
        "text": item.text,
    }
    extras = {
        "chat": item.chat_name,
        "subreddit": item.subreddit,
        "collection": item.collection_name,
        "link_url": item.link_url,
        "origin": item.origin,
        "thread_root_id": item.thread_root_id,
        "in_reply_to": item.in_reply_to_status_id,
        "quoted": item.quoted_tweet_id,
        "media_description": item.vlm_description,
        "likes": item.like_count,
        "replies": item.reply_count,
    }
    out.update({name: value for name, value in extras.items() if value is not None})
    # False marks context the expander adopted (a parent, the author's own replies) rather
    # than something the user liked/saved themselves
    out["is_seed"] = is_seed(item)
    if item.has_media:
        # A signed link per file on disk, fetchable without a session for links.TTL; a file
        # can be missing (never downloaded, or cleaned up after upload)
        out["media"] = [
            {"index": index, "type": kind}
            | (
                {"url": links.url(item.platform, item.item_id, index)}
                if index < len(item.local_paths) and item.local_paths[index].exists()
                else {"missing": True}
            )
            for index, kind in enumerate(_media_kinds(item))
        ]
    return out


def _media_kinds(item: Item) -> list[str | None]:
    count = max(item.media_count, len(item.local_paths))
    return [item.media_types[i] if i < len(item.media_types) else None for i in range(count)]


async def search_archive(
    query: str = "",
    semantic: bool = False,
    sort: SearchSort = SearchSort.RELEVANCE,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Search or browse the archive (twitter, reddit, instagram, whatsapp).

    Words: every word must match, "quoted phrases" match in order, word* matches a prefix,
    -word excludes, `a OR b` takes either, NEAR(a b, 5) keeps words within five of each
    other. Words match post text and the captions of images and videos alike; each hit says
    where it matched (matched_text / matched_media).
    Operators, alone or with words:
    platform:twitter|reddit|instagram|whatsapp  from:author  in:"whatsapp chat name"
    r:subreddit  category:x  origin:x  shared:user  after:YYYY-MM-DD  before:YYYY-MM-DD
    likes:>N  views:>N  has:media|link|image|video|gif|audio|sticker|document
    -has:media|link  is:post|reply|comment|retweet|quote (negatable with -)
    is:reel|carousel|group|dm|liked  match:text|media|names.
    An empty or operators-only query browses what matches, newest first. Page with offset.
    semantic=True ranks by meaning when embeddings are configured (no offset).
    Each item lists its media files with a signed url (valid 24 hours, no sign-in needed)
    to fetch the original image, video, audio or document. Open a hit with open_item for
    its neighbours, replies or whole discussion."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if semantic:
        found = await semantic_search.find(reader, query, ItemFilters(), limit=limit)
        return [{**_item(item), "matched": hit.caption} for hit, item in found]
    hits = await reader.search(query, ItemFilters(), sort, limit=limit, offset=offset)
    return [
        {
            **_item(h.item),
            **({"matched_text": bracketed(h.snippet)} if h.snippet else {}),
            **({"matched_media": bracketed(h.media_snippet)} if h.media_snippet else {}),
        }
        for h in hits
    ]


async def open_item(platform: str, item_id: str, view: Literal["item", "discussion"] = "item") -> dict[str, Any]:
    """Open one item from search_archive.

    view="item": the item with its neighbours (the post it replies to, quotes or retweets,
    what led to it being archived) and its direct replies.
    view="discussion": the whole conversation around it. For twitter and reddit that is the
    ancestors (root first) and the nested reply tree; for whatsapp, that chat's messages on
    the same day. Entries carry is_seed: false means the archiver pulled it in as context,
    not that the user liked or saved it."""
    item = await reader.get(platform, item_id)
    if item is None:
        raise ValueError(f"no {platform} item {item_id}")
    if view == "discussion":
        return await _discussion(platform, item)
    opened = _item(item)
    related = await reader.related(platform, item)
    opened.update({name: _item(neighbour) for name, neighbour in related.items() if neighbour})
    if replies := await reader.replies(platform, item_id, limit=25):
        opened["reply_items"] = [_item(r) for r in replies]
    return opened


async def _discussion(platform: str, item: Item) -> dict[str, Any]:
    if platform in conversations.CONVERSATION_PLATFORMS:
        tree = await conversations.load(reader, platform, item.item_id)

        def node(entry: conversations.ConversationNode) -> dict[str, Any]:
            return {**_item(entry.item), "replies": [node(r) for r in entry.replies]}

        return {
            "focus": _item(tree.focus),
            "ancestors": [_item(a) for a in tree.ancestors],
            "missing_parent": tree.missing_parent,
            "replies": [node(r) for r in tree.replies],
        }
    if item.thread_root_id:
        return {
            "focus_id": item.item_id,
            "messages": [_item(m) for m in await reader.thread(platform, item.thread_root_id)],
        }
    raise ValueError(f"{platform} items have no discussion; use view='item'")


async def archive_overview() -> dict[str, Any]:
    """What the archive holds: per platform the totals, categories and date range, and the
    WhatsApp chats by name (for in:"chat name" searches), most recently active first."""
    platforms = []
    for platform in await reader.present():
        stats = await reader.stats(platform)
        platforms.append(
            {
                "platform": platform,
                "total": stats.total,
                "categories": stats.categories,
                "authors": stats.authors,
                "oldest": stats.oldest.isoformat() if stats.oldest else None,
                "newest": stats.newest.isoformat() if stats.newest else None,
            }
        )
    chats = [
        {
            "name": chat.name or chat.chat_id,
            "kind": chat.category,
            "messages": chat.message_count,
            "last_at": chat.last_at.date().isoformat() if chat.last_at else None,
        }
        for chat in await reader.chats("whatsapp")
    ]
    return {"platforms": platforms, "whatsapp_chats": chats}


def create(provider: OAuthAuthorizationServerProvider, auth: AuthSettings) -> MCPServer:
    server = MCPServer(
        "social-archiver",
        instructions="A personal archive of twitter, reddit, instagram and whatsapp: liked/saved "
        "posts, chat history, and everything each pulled in. Search it, browse it, or pull whole "
        "threads and conversations.",
        auth_server_provider=provider,
        auth=auth,
    )
    server.add_tool(search_archive, annotations=_READ_ONLY)
    server.add_tool(archive_overview, annotations=_READ_ONLY)
    server.add_tool(open_item, annotations=_READ_ONLY)
    return server
