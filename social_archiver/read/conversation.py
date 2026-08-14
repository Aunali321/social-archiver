"""Assembles an archived conversation around one item: the ancestor chain above it, the
reply tree below it, and for every member whether the user actually acted on it (seed) or
whether the expander adopted it as context. One implementation feeds the API, the UI and
the MCP server, so every consumer sees the same tree.
"""

from dataclasses import dataclass, field

from social_archiver.core.database import Item
from social_archiver.read.store import ArchiveReader

# Platforms whose conversation_id names a bounded discussion. WhatsApp's names a whole chat,
# which is a transcript, not a tree — the chat view serves it.
CONVERSATION_PLATFORMS = ("twitter", "reddit")


@dataclass(slots=True)
class ConversationNode:
    item: Item
    replies: list["ConversationNode"] = field(default_factory=list)


@dataclass(slots=True)
class Conversation:
    focus: Item
    ancestors: list[Item]  # root first, immediate parent last
    missing_parent: bool  # the chain points above the archive's horizon
    replies: list[ConversationNode]  # tree below the focus, chronological at every level


async def load(reader: ArchiveReader, platform: str, item_id: str) -> Conversation | None:
    focus = await reader.get(platform, item_id)
    if focus is None:
        return None
    members = await reader.conversation(platform, focus.conversation_id or focus.item_id)
    if all(member.item_id != focus.item_id for member in members):
        members.append(focus)
    return _assemble(focus, members)


def _assemble(focus: Item, members: list[Item]) -> Conversation:
    by_id = {member.item_id: member for member in members}
    children: dict[str, list[Item]] = {}
    for member in members:
        if member.in_reply_to_status_id:
            children.setdefault(member.in_reply_to_status_id, []).append(member)

    ancestors: list[Item] = []
    seen = {focus.item_id}
    parent_ref = focus.in_reply_to_status_id
    while parent_ref and parent_ref in by_id and parent_ref not in seen:
        parent = by_id[parent_ref]
        ancestors.append(parent)
        seen.add(parent_ref)
        parent_ref = parent.in_reply_to_status_id
    ancestors.reverse()

    def tree(item: Item, path: set[str]) -> list[ConversationNode]:
        return [
            ConversationNode(item=child, replies=tree(child, path | {child.item_id}))
            for child in children.get(item.item_id, [])
            if child.item_id not in path
        ]

    return Conversation(
        focus=focus,
        ancestors=ancestors,
        missing_parent=bool(parent_ref and parent_ref not in by_id),
        replies=tree(focus, {focus.item_id}),
    )
