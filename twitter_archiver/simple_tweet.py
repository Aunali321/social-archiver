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
    """Simplified tweet with fields needed for download/upload."""
    id: str
    text: str
    author_username: str
    author_name: str
    author_id: Optional[str] = None
    created_at: Optional[datetime] = None
    reply_count: Optional[int] = None
    retweet_count: Optional[int] = None
    like_count: Optional[int] = None
    conversation_id: Optional[str] = None
    in_reply_to_status_id: Optional[str] = None
    media: List[TweetMedia] = field(default_factory=list)
    quoted_tweet: Optional["SimpleTweet"] = None

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
        if data.get("quoted_tweet"):
            try:
                quoted_tweet = cls.from_api_dict(data["quoted_tweet"])
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
            conversation_id=data.get("conversation_id"),
            in_reply_to_status_id=data.get("in_reply_to_status_id"),
            media=media_list,
            quoted_tweet=quoted_tweet,
        )
