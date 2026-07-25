"""Simplified tweet data model for the Twitter archiver."""

from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from social_archiver.core.database import ArchiveStatus, Item, StageStatus


@dataclass
class TweetMedia:
    type: str  # photo, video, animated_gif
    url: str  # media_url_https (photo thumbnail / poster)
    video_url: str | None = None  # best quality mp4 for video/gif
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None


@dataclass
class SimpleTweet:
    """Simplified tweet with fields needed for download/upload and full linking."""

    id: str
    text: str
    author_username: str
    author_name: str
    author_id: str | None = None
    created_at: datetime | None = None

    reply_count: int | None = None
    retweet_count: int | None = None
    like_count: int | None = None
    quote_count: int | None = None
    bookmark_count: int | None = None
    view_count: int | None = None

    # Relationship IDs, for reconstructing the Twitter frontend view
    conversation_id: str | None = None
    in_reply_to_status_id: str | None = None
    quoted_tweet_id: str | None = None
    retweeted_tweet_id: str | None = None
    is_retweet: bool = False

    media: list[TweetMedia] = field(default_factory=list)
    quoted_tweet: "SimpleTweet | None" = None

    # text holds the rendered article body (title + markdown) when is_article
    is_article: bool = False

    # Origin tracking: liked, bookmarked, thread, parent, quoted, linked, retweet
    origin: str = "liked"
    discovered_via_tweet_id: str | None = None

    # Tweet existed in the graph but was unavailable (deleted/protected/suspended);
    # text carries the tombstone reason
    is_tombstone: bool = False

    # Thread metadata, set during expansion
    thread_position: str | None = None  # root, middle, end, standalone
    has_self_replies: bool | None = None
    thread_root_id: str | None = None

    @property
    def has_media(self) -> bool:
        return bool(self.media)

    @property
    def has_video(self) -> bool:
        return any(m.type in ("video", "animated_gif") for m in self.media)

    @property
    def has_photo(self) -> bool:
        return any(m.type == "photo" for m in self.media)

    @property
    def media_types(self) -> list[str]:
        return list({m.type for m in self.media})

    @property
    def media_urls(self) -> list[str]:
        """Original media URLs for archival (photo :orig, video best mp4)."""
        urls = []
        for m in self.media:
            if m.type == "photo":
                urls.append(f"{m.url}:orig")
            elif m.type in ("video", "animated_gif") and m.video_url:
                urls.append(m.video_url)
            else:
                urls.append(m.url)
        return urls

    @property
    def post_url(self) -> str:
        if self.is_tombstone:
            return f"https://x.com/i/status/{self.id}"
        return f"https://x.com/{self.author_username}/status/{self.id}"

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> "SimpleTweet":
        """Build a SimpleTweet from the dict returned by TwitterClient's parsers."""
        created_at = None
        if raw_date := data.get("created_at"):
            try:
                created_at = parsedate_to_datetime(raw_date)
            except Exception:
                created_at = datetime.now()

        media_list = [
            TweetMedia(
                type=m.get("type", "photo"),
                url=m.get("url", ""),
                video_url=m.get("video_url"),
                width=m.get("width"),
                height=m.get("height"),
                duration_ms=m.get("duration_ms"),
            )
            for m in data.get("media") or []
        ]

        quoted_tweet = None
        quoted_tweet_id = None
        if data.get("quoted_tweet"):
            try:
                quoted_tweet = cls.from_api_dict(data["quoted_tweet"])
                quoted_tweet_id = quoted_tweet.id
            except Exception:
                pass

        return cls(
            id=data.get("id", ""),
            text=data.get("text", ""),
            author_username=data.get("author_username", "unknown"),
            author_name=data.get("author_name", "unknown"),
            author_id=data.get("author_id"),
            created_at=created_at,
            reply_count=data.get("reply_count"),
            retweet_count=data.get("retweet_count"),
            like_count=data.get("like_count"),
            quote_count=data.get("quote_count"),
            bookmark_count=data.get("bookmark_count"),
            view_count=data.get("view_count"),
            conversation_id=data.get("conversation_id"),
            in_reply_to_status_id=data.get("in_reply_to_status_id"),
            quoted_tweet_id=quoted_tweet_id,
            retweeted_tweet_id=data.get("retweeted_tweet_id"),
            is_retweet=data.get("is_retweet", False),
            media=media_list,
            quoted_tweet=quoted_tweet,
            is_article=data.get("is_article", False),
        )

    def to_item(self, category: str) -> Item:
        """Tombstones are record-only: nothing to download, upload, or embed.
        Text-only tweets are fully archived the moment they are recorded;
        tweets with media await the download step."""
        if self.is_tombstone:
            archive_status, stage_status = ArchiveStatus.TOMBSTONE, StageStatus.SKIPPED
        else:
            archive_status = ArchiveStatus.PENDING if self.has_media else ArchiveStatus.ARCHIVED
            stage_status = StageStatus.PENDING

        return Item(
            item_id=self.id,
            platform="twitter",
            category=category,
            author_username=self.author_username,
            author_id=self.author_id,
            text=self.text,
            is_article=self.is_article,
            post_url=self.post_url,
            created_at=self.created_at,
            has_media=self.has_media,
            media_count=len(self.media),
            media_types=self.media_types,
            media_urls=self.media_urls,
            conversation_id=self.conversation_id,
            in_reply_to_status_id=self.in_reply_to_status_id,
            quoted_tweet_id=self.quoted_tweet_id,
            retweeted_tweet_id=self.retweeted_tweet_id,
            is_retweet=self.is_retweet,
            origin=self.origin,
            discovered_via_item_id=self.discovered_via_tweet_id,
            thread_position=self.thread_position,
            has_self_replies=self.has_self_replies,
            thread_root_id=self.thread_root_id,
            reply_count=self.reply_count,
            retweet_count=self.retweet_count,
            like_count=self.like_count,
            quote_count=self.quote_count,
            bookmark_count=self.bookmark_count,
            view_count=self.view_count,
            archive_status=archive_status,
            upload_status=stage_status,
            embed_status=stage_status,
        )
