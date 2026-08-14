"""MCP server over the read layer, so an assistant can query the archive.

Read-only by construction: every tool goes through ArchiveReader's mode=ro connections.
Runs on stdio — `social-archiver mcp` — pointed at by a client config, e.g.:
    {"mcpServers": {"social-archiver": {"command": "social-archiver", "args": ["mcp"]}}}
"""

from datetime import datetime
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from social_archiver.core import config
from social_archiver.core.config import PLATFORMS
from social_archiver.core.database import Item
from social_archiver.read import ArchiveReader, ItemFilters
from social_archiver.read import conversation as conversations
from social_archiver.read import semantic as semantic_search
from social_archiver.read.models import is_seed

mcp = MCPServer(
    "social-archiver",
    instructions="A personal archive of twitter, reddit, instagram and whatsapp: liked/saved "
    "posts, chat history, and everything each pulled in. Search it, browse it, or pull whole "
    "threads and conversations.",
)
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


@mcp.tool(annotations=_READ_ONLY)
async def search_archive(
    query: str, platform: str | None = None, semantic: bool = False, limit: int = 20
) -> list[dict[str, Any]]:
    """Search the social media archive (twitter, reddit, instagram, whatsapp). Full-text by
    default; semantic=True uses vector search when embeddings are configured."""
    selected = _platforms(platform)
    if semantic:
        hits = await semantic_search.search(query, selected, limit=limit)
        found = [(await reader.get(h.platform, h.item_id), h.caption) for h in hits]
        return [{**_item(item), "matched": caption} for item, caption in found if item]
    hits = await reader.search(query, ItemFilters(platforms=selected), limit=limit)
    return [{**_item(h.item), "matched": h.snippet} for h in hits]


@mcp.tool(annotations=_READ_ONLY)
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


@mcp.tool(annotations=_READ_ONLY)
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


@mcp.tool(annotations=_READ_ONLY)
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


@mcp.tool(annotations=_READ_ONLY)
async def get_thread(platform: str, root_id: str) -> list[dict[str, Any]]:
    """A whole thread or conversation in chronological order. For WhatsApp, root_id is
    '<chat_jid>:<YYYY-MM-DD>' — one chat-day."""
    return [_item(i) for i in await reader.thread(platform, root_id)]


@mcp.tool(annotations=_READ_ONLY)
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


@mcp.tool(annotations=_READ_ONLY)
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


def main():
    mcp.run()


if __name__ == "__main__":
    main()
