"""
Simplified tweet data model for the Twitter archiver.
Analogous to insta_archiver's SimpleMedia.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime
from email.utils import parsedate_to_datetime


@dataclass
class TweetMedia:
    """A media attachment on a tweet."""
    type: str  # photo, video, animated_gif
    url: str  # media_url_https (photo thumbnail / poster)
    video_url: Optional[str] = None  # best quality mp4 for video/gif
    width: Optional[int] = None
    height: Optional[int] = None
    duration_ms: Optional[int] = None


@dataclass
class SimpleTweet:
    """Simplified tweet with fields needed for download/upload and full linking."""
    id: str
    text: str
    author_username: str
    author_name: str
    author_id: Optional[str] = None
    created_at: Optional[datetime] = None

    # Engagement counts
    reply_count: Optional[int] = None
    retweet_count: Optional[int] = None
    like_count: Optional[int] = None
    quote_count: Optional[int] = None
    bookmark_count: Optional[int] = None
    view_count: Optional[int] = None

    # Relationship IDs (for linking in DB / frontend reconstruction)
    conversation_id: Optional[str] = None
    in_reply_to_status_id: Optional[str] = None
    quoted_tweet_id: Optional[str] = None
    retweeted_tweet_id: Optional[str] = None
    is_retweet: bool = False

    # Media
    media: List[TweetMedia] = field(default_factory=list)

    # Nested tweet objects (for immediate access before DB insert)
    quoted_tweet: Optional["SimpleTweet"] = None

    # Origin tracking
    origin: str = "liked"  # liked, thread, parent, quoted, liked_reply, retweet
    discovered_via_tweet_id: Optional[str] = None

    # Thread metadata (set during expansion)
    thread_position: Optional[str] = None  # root, middle, end, standalone
    has_self_replies: Optional[bool] = None
    thread_root_id: Optional[str] = None

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
    def media_types(self) -> List[str]:
        return list({m.type for m in self.media})

    @property
    def media_urls(self) -> List[str]:
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
        return f"https://x.com/{self.author_username}/status/{self.id}"

    @classmethod
    def from_api_dict(cls, data: Dict[str, Any]) -> "SimpleTweet":
        """Create a SimpleTweet from the dict returned by TwitterClient's parsers."""
        # Parse created_at from Twitter's date format
        created_at = None
        raw_date = data.get("created_at")
        if raw_date:
            try:
                created_at = parsedate_to_datetime(raw_date)
            except Exception:
                created_at = datetime.now()

        # Parse media
        media_list = []
        for m in data.get("media") or []:
            media_list.append(
                TweetMedia(
                    type=m.get("type", "photo"),
                    url=m.get("url", ""),
                    video_url=m.get("video_url"),
                    width=m.get("width"),
                    height=m.get("height"),
                    duration_ms=m.get("duration_ms"),
                )
            )

        # Parse quoted tweet
        quoted_tweet = None
        quoted_tweet_id = None
        if data.get("quoted_tweet"):
            try:
                quoted_tweet = cls.from_api_dict(data["quoted_tweet"])
                quoted_tweet_id = quoted_tweet.id
            except Exception:
                pass

        # Extract retweeted_tweet_id if this is a retweet
        retweeted_tweet_id = data.get("retweeted_tweet_id")
        is_retweet = data.get("is_retweet", False)

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
            retweeted_tweet_id=retweeted_tweet_id,
            is_retweet=is_retweet,
            media=media_list,
            quoted_tweet=quoted_tweet,
        )

    def to_db_dict(self) -> Dict[str, Any]:
        """Convert to a dict suitable for Database.insert_tweet / bulk_insert_tweets."""
        return {
            "tweet_id": self.id,
            "author_username": self.author_username,
            "author_name": self.author_name,
            "author_id": self.author_id,
            "tweet_text": self.text,
            "post_url": self.post_url,
            "created_at": self.created_at,
            "conversation_id": self.conversation_id,
            "in_reply_to_status_id": self.in_reply_to_status_id,
            "quoted_tweet_id": self.quoted_tweet_id,
            "retweeted_tweet_id": self.retweeted_tweet_id,
            "is_retweet": self.is_retweet,
            "reply_count": self.reply_count,
            "retweet_count": self.retweet_count,
            "like_count": self.like_count,
            "quote_count": self.quote_count,
            "bookmark_count": self.bookmark_count,
            "view_count": self.view_count,
            "has_media": self.has_media,
            "media_count": len(self.media),
            "media_types": self.media_types if self.media else None,
            "media_urls": self.media_urls if self.media else None,
            "origin": self.origin,
            "discovered_via_tweet_id": self.discovered_via_tweet_id,
            "thread_position": self.thread_position,
            "has_self_replies": self.has_self_replies,
            "thread_root_id": self.thread_root_id,
            "status": "pending",
        }
