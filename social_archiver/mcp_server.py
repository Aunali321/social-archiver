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
from social_archiver.read.models import Profile, SearchSort, bracketed, is_seed
from social_archiver.read.profiles import Authors, ProfileReader

reader = ArchiveReader(config.DATA_DIR)
profiles = ProfileReader(reader)

_READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


def _author(item: Item, authors: Authors) -> dict[str, Any]:
    """The account as it is named today, which can differ from the handle the post carried."""
    ref = authors.get((item.platform, item.author_id)) if item.author_id else None
    if ref is None:
        return {"handle": item.author_username}
    return {"handle": ref.handle, "name": ref.display_name, "profile_id": ref.profile_id}


def _item(item: Item, authors: Authors) -> dict[str, Any]:
    """Compact and text-first: what a model needs to reason about a post, not pipeline state."""
    out: dict[str, Any] = {
        "platform": item.platform,
        "item_id": item.item_id,
        "author": _author(item, authors),
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


def _profile(profile: Profile) -> dict[str, Any]:
    current = profile.current
    out: dict[str, Any] = {
        "platform": profile.platform,
        "profile_id": profile.profile_id,
        "handle": current.handle,
        "name": current.display_name,
        "bio": current.bio,
        "location": current.location,
        "website": current.website,
        "status": profile.status,
        "is_you": profile.is_self,
        "followers": profile.followers,
        "following": profile.following,
        "joined": profile.joined_at.date().isoformat() if profile.joined_at else None,
        "archived_items": profile.encounters.total,
        "items_by_origin": profile.encounters.origins,
        "history": [
            {
                "from": version.first_seen.date().isoformat(),
                "to": version.last_seen.date().isoformat(),
                "handle": version.handle,
                "name": version.display_name,
                "bio": version.bio,
            }
            for version in profile.versions
        ],
    }
    return {name: value for name, value in out.items() if value not in (None, "", [], {})}


async def search_archive(
    query: str = "",
    semantic: bool = False,
    people: bool = False,
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
    its neighbours, replies or whole discussion.
    from: matches every handle and name an account has gone by, so a renamed account is
    found by its old name. people=True searches accounts instead of items: query words
    match handles, names and bios (past ones too), most archived first; an empty query
    lists the most archived accounts. A person's items are then from:handle."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if people:
        hits = await profiles.search(query, tuple(await reader.present()), limit=limit)
        found = [await profiles.get(hit.author.platform, hit.author.profile_id) for hit in hits]
        return [_profile(profile) for profile in found if profile]
    if semantic:
        found_items = await semantic_search.find(reader, query, ItemFilters(), limit=limit)
        authors = await profiles.authors([item for _, item in found_items])
        return [{**_item(item, authors), "matched": hit.caption} for hit, item in found_items]
    hits = await reader.search(query, ItemFilters(), sort, limit=limit, offset=offset)
    authors = await profiles.authors([h.item for h in hits])
    return [
        {
            **_item(h.item, authors),
            **({"matched_text": bracketed(h.snippet)} if h.snippet else {}),
            **({"matched_media": bracketed(h.media_snippet)} if h.media_snippet else {}),
        }
        for h in hits
    ]


async def open_item(
    platform: str, item_id: str, view: Literal["item", "discussion", "author"] = "item"
) -> dict[str, Any]:
    """Open one item from search_archive.

    view="item": the item with its neighbours (the post it replies to, quotes or retweets,
    what led to it being archived) and its direct replies.
    view="discussion": the whole conversation around it. For twitter and reddit that is the
    ancestors (root first) and the nested reply tree; for whatsapp, that chat's messages on
    the same day. Entries carry is_seed: false means the archiver pulled it in as context,
    not that the user liked or saved it.
    view="author": the account that posted it, with every handle, name and bio it has had
    and how many of its items the archive holds, by origin."""
    item = await reader.get(platform, item_id)
    if item is None:
        raise ValueError(f"no {platform} item {item_id}")
    if view == "discussion":
        return await _discussion(platform, item)
    if view == "author":
        profile = await profiles.get(platform, item.author_id) if item.author_id else None
        if profile is None:
            raise ValueError(f"no account is known behind {platform} item {item_id}")
        return _profile(profile)
    related = await reader.related(platform, item)
    replies = await reader.replies(platform, item_id, limit=25)
    authors = await profiles.authors([item, *(n for n in related.values() if n), *replies])
    opened = _item(item, authors)
    opened.update({name: _item(neighbour, authors) for name, neighbour in related.items() if neighbour})
    if replies:
        opened["reply_items"] = [_item(r, authors) for r in replies]
    return opened


async def _discussion(platform: str, item: Item) -> dict[str, Any]:
    if platform in conversations.CONVERSATION_PLATFORMS:
        tree = await conversations.load(reader, platform, item.item_id)

        def members(nodes: list[conversations.ConversationNode]) -> list[Item]:
            return [entry for node in nodes for entry in (node.item, *members(node.replies))]

        authors = await profiles.authors([tree.focus, *tree.ancestors, *members(tree.replies)])

        def node(entry: conversations.ConversationNode) -> dict[str, Any]:
            return {**_item(entry.item, authors), "replies": [node(r) for r in entry.replies]}

        return {
            "focus": _item(tree.focus, authors),
            "ancestors": [_item(a, authors) for a in tree.ancestors],
            "missing_parent": tree.missing_parent,
            "replies": [node(r) for r in tree.replies],
        }
    if item.thread_root_id:
        messages = await reader.thread(platform, item.thread_root_id)
        authors = await profiles.authors(messages)
        return {"focus_id": item.item_id, "messages": [_item(m, authors) for m in messages]}
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
