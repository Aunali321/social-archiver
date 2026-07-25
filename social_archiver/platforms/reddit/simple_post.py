"""Simplified Reddit data model. Both submissions and comments become one
`RedditItem`, mirroring how the Twitter archiver flattens tweets."""

from dataclasses import dataclass, field
from datetime import datetime

from social_archiver.core.database import ArchiveStatus, Item, StageStatus

PLATFORM = "reddit"


@dataclass(slots=True)
class RedditMedia:
    type: str  # image, gif, video
    url: str  # direct file URL for image/gif; a resolvable page/stream URL for video


@dataclass(slots=True)
class RedditItem:
    fullname: str  # t3_xxx (post) or t1_xxx (comment)
    kind: str  # "post" | "comment"
    author: str
    subreddit: str
    permalink: str
    created_at: datetime | None = None
    score: int | None = None

    title: str | None = None  # posts only
    body: str | None = None  # selftext (post) or comment body
    num_comments: int | None = None  # posts only
    over_18: bool = False
    link_flair: str | None = None

    # Thread graph. A post's submission is itself; comments carry their post and parent.
    submission_fullname: str | None = None
    parent_fullname: str | None = None

    link_url: str | None = None  # where a link post points, which the permalink does not record

    media: list[RedditMedia] = field(default_factory=list)

    origin: str = "saved"  # seed origin (category) or context origin (submission/parent)
    discovered_via_fullname: str | None = None
    is_tombstone: bool = False

    @property
    def has_media(self) -> bool:
        return bool(self.media)

    @property
    def media_types(self) -> list[str]:
        return list({m.type for m in self.media})

    @property
    def media_urls(self) -> list[str]:
        return [m.url for m in self.media]

    @property
    def text(self) -> str | None:
        if self.kind == "post":
            title = f"**{self.title}**" if self.title else None
            return "\n\n".join(s for s in (title, self.body) if s) or None
        return self.body

    def to_item(self, category: str) -> Item:
        """Tombstones are record-only. Text-only items are fully archived on
        insert; items with media wait for the download step."""
        if self.is_tombstone:
            archive_status, stage_status = ArchiveStatus.TOMBSTONE, StageStatus.SKIPPED
        else:
            archive_status = ArchiveStatus.PENDING if self.has_media else ArchiveStatus.ARCHIVED
            stage_status = StageStatus.PENDING

        return Item(
            item_id=self.fullname,
            platform=PLATFORM,
            category=category,
            author_username=self.author,
            post_url=self.permalink,
            text=self.text,
            created_at=self.created_at,
            has_media=self.has_media,
            media_count=len(self.media),
            media_types=self.media_types,
            media_urls=self.media_urls,
            subreddit=self.subreddit,
            link_url=self.link_url,
            conversation_id=self.submission_fullname,
            thread_root_id=self.submission_fullname,
            in_reply_to_status_id=self.parent_fullname,
            origin=self.origin,
            discovered_via_item_id=self.discovered_via_fullname,
            like_count=self.score,
            reply_count=self.num_comments,
            archive_status=archive_status,
            upload_status=stage_status,
            embed_status=stage_status,
        )
