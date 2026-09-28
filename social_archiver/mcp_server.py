"""MCP server over the read layer, so an assistant can query the archive.

Read-only by construction: every tool goes through ArchiveReader's mode=ro connections.
Served over streamable HTTP at /mcp by the web process (see social_archiver.api), behind the
same owner sign-in as the UI, so any MCP host that speaks OAuth can connect by URL alone.
"""

from datetime import datetime
from typing import Any

from mcp.server import MCPServer
from mcp.server.auth.provider import OAuthAuthorizationServerProvider
from mcp.server.auth.settings import AuthSettings
from mcp.types import ToolAnnotations

from social_archiver.core import config
from social_archiver.core.config import PLATFORMS
from social_archiver.core.database import Item
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
        out["media"] = item.media_types or item.media_count
    return out


def _platforms(platform: str | None) -> tuple[str, ...]:
    if platform and platform not in PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}; expected one of {', '.join(PLATFORMS)}")
    return (platform,) if platform else ()


async def search_archive(
    query: str,
    platform: str | None = None,
    semantic: bool = False,
    sort: SearchSort = SearchSort.RELEVANCE,
    author: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    has_media: bool | None = None,
    seeds_only: bool = False,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search the social media archive (twitter, reddit, instagram, whatsapp).

    Full-text by default: every word must match, "quoted phrases" match in order, word*
    matches a prefix, -word excludes, `a OR b` takes either and NEAR(a b, 5) keeps words
    within five of each other. The query also takes operators, alone or with words:
    from:author  in:"whatsapp chat name"  r:subreddit  platform:reddit  category:x
    origin:x  shared:user  after:YYYY-MM-DD  before:YYYY-MM-DD  likes:>N  views:>N
    has:media|link|image|video|gif|audio|sticker|document  -has:media|link
    is:post|reply|comment|retweet|quote (negatable)  is:reel|carousel|group|dm|liked
    match:text|media|names (restrict words to post text, media descriptions, or names).
    semantic=True uses vector search when embeddings are configured and always ranks by
    relevance. Dates are ISO (YYYY-MM-DD)."""
    filters = ItemFilters(
        platforms=_platforms(platform),
        author=author,
        has_media=has_media,
        seeds_only=seeds_only,
        date_from=datetime.fromisoformat(date_from) if date_from else None,
        date_to=datetime.fromisoformat(date_to) if date_to else None,
    )
    if semantic:
        found = await semantic_search.find(reader, query, filters, limit=limit)
        return [{**_item(item), "matched": hit.caption} for hit, item in found]
    hits = await reader.search(query, filters, sort, limit=limit)
    return [
        {
            **_item(h.item),
            **({"matched_text": bracketed(h.snippet)} if h.snippet else {}),
            **({"matched_media": bracketed(h.media_snippet)} if h.media_snippet else {}),
        }
        for h in hits
    ]


async def list_items(
    platform: str | None = None,
    category: str | None = None,
    author: str | None = None,
    chat: str | None = None,
    subreddit: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    seeds_only: bool = False,
    cursor: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Browse archived items newest-first. Dates are ISO (YYYY-MM-DD). `chat` is a WhatsApp
    chat id from list_chats. seeds_only=True hides expander-adopted context, leaving only what
    the user liked/saved. Pass the returned next_cursor to continue."""
    filters = ItemFilters(
        platforms=_platforms(platform),
        category=category,
        author=author,
        chat=chat,
        subreddit=subreddit,
        seeds_only=seeds_only,
        date_from=datetime.fromisoformat(date_from) if date_from else None,
        date_to=datetime.fromisoformat(date_to) if date_to else None,
    )
    page = await reader.list_items(filters, cursor=cursor, limit=limit)
    return {"items": [_item(i) for i in page.items], "next_cursor": page.next_cursor}


async def get_item(platform: str, item_id: str) -> dict[str, Any]:
    """One item with its graph neighbours: parent, quoted/retweeted post, what led to it
    being archived, and direct replies."""
    item = await reader.get(platform, item_id)
    if item is None:
        raise ValueError(f"no {platform} item {item_id}")
    related = await reader.related(platform, item)
    replies = await reader.replies(platform, item_id, limit=25)
    out = _item(item)
    out.update({name: _item(neighbour) for name, neighbour in related.items() if neighbour})
    if replies:
        out["reply_items"] = [_item(r) for r in replies]
    return out


async def get_conversation(platform: str, item_id: str) -> dict[str, Any]:
    """A twitter/reddit discussion assembled around one item: `ancestors` (root first) above
    it, `replies` as a nested tree below it. Each entry carries is_seed — False means the
    archiver pulled it in as context, not that the user liked/saved it."""
    if platform not in conversations.CONVERSATION_PLATFORMS:
        raise ValueError(f"{platform} has no conversation trees; use get_thread or list_items(chat=...)")
    tree = await conversations.load(reader, platform, item_id)
    if tree is None:
        raise ValueError(f"no {platform} item {item_id}")

    def node(entry: conversations.ConversationNode) -> dict[str, Any]:
        return {**_item(entry.item), "replies": [node(r) for r in entry.replies]}

    return {
        "focus": _item(tree.focus),
        "ancestors": [_item(a) for a in tree.ancestors],
        "missing_parent": tree.missing_parent,
        "replies": [node(r) for r in tree.replies],
    }


async def get_thread(platform: str, root_id: str) -> list[dict[str, Any]]:
    """A whole thread or conversation in chronological order. For WhatsApp, root_id is
    '<chat_jid>:<YYYY-MM-DD>' — one chat-day."""
    return [_item(i) for i in await reader.thread(platform, root_id)]


async def list_chats(platform: str = "whatsapp") -> list[dict[str, Any]]:
    """Conversations with name, message count and latest message, newest first."""
    return [
        {
            "chat_id": chat.chat_id,
            "name": chat.name or None,
            "messages": chat.message_count,
            "last_at": chat.last_at.isoformat() if chat.last_at else None,
            "last_text": chat.last_text,
        }
        for chat in await reader.chats(platform)
    ]


async def archive_stats() -> list[dict[str, Any]]:
    """What the archive holds: totals, categories and date range per platform."""
    out = []
    for platform in await reader.present():
        stats = await reader.stats(platform)
        out.append(
            {
                "platform": platform,
                "total": stats.total,
                "categories": stats.categories,
                "authors": stats.authors,
                "oldest": stats.oldest.isoformat() if stats.oldest else None,
                "newest": stats.newest.isoformat() if stats.newest else None,
            }
        )
    return out


def create(provider: OAuthAuthorizationServerProvider, auth: AuthSettings) -> MCPServer:
    server = MCPServer(
        "social-archiver",
        instructions="A personal archive of twitter, reddit, instagram and whatsapp: liked/saved "
        "posts, chat history, and everything each pulled in. Search it, browse it, or pull whole "
        "threads and conversations.",
        auth_server_provider=provider,
        auth=auth,
    )
    for tool in (search_archive, list_items, get_item, get_conversation, get_thread, list_chats, archive_stats):
        server.add_tool(tool, annotations=_READ_ONLY)
    return server
