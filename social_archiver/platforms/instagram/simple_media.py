"""Forgiving media types for the Instagram archiver — more lenient than instagrapi's strict Pydantic models."""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SimpleUser:
    pk: str
    username: str
    full_name: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "SimpleUser":
        return cls(
            pk=str(data.get("pk", data.get("id", ""))),
            username=data.get("username", "unknown"),
            full_name=data.get("full_name"),
        )


@dataclass
class SimpleResource:
    """One item of a carousel/album."""
    pk: str
    media_type: int  # 1=photo, 2=video
    video_url: str | None = None
    thumbnail_url: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "SimpleResource":
        video_url = None
        if data.get("video_versions"):
            video_url = max(data["video_versions"], key=lambda v: v.get("height", 0) * v.get("width", 0)).get("url")

        thumbnail_url = None
        candidates = (data.get("image_versions2") or {}).get("candidates", [])
        if candidates:
            thumbnail_url = max(candidates, key=lambda c: c.get("height", 0) * c.get("width", 0)).get("url")

        return cls(
            pk=str(data.get("pk", data.get("id", ""))),
            media_type=data.get("media_type", 1),
            video_url=video_url,
            thumbnail_url=thumbnail_url,
        )


@dataclass
class SimpleMedia:
    pk: str
    id: str  # full media ID (pk_userid)
    code: str  # shortcode for URL
    media_type: int  # 1=photo, 2=video, 8=album
    caption_text: str
    user: SimpleUser
    taken_at: datetime
    video_url: str | None = None
    thumbnail_url: str | None = None
    resources: list[SimpleResource] = field(default_factory=list)  # for albums
    product_type: str = "feed"  # feed, clips, igtv, etc.
    collection_name: str | None = None  # for saved posts
    shared_by_username: str | None = None  # for DM shared posts

    @classmethod
    def from_dict(cls, data: dict) -> "SimpleMedia":
        """Extract media from a raw API response, tolerating the None values that
        break instagrapi's strict Pydantic extraction."""
        from instagrapi.utils import InstagramIdCodec

        pk = str(data.get("pk", ""))
        user = SimpleUser.from_dict(data.get("user", {}))
        media_id = data.get("id", f"{pk}_{user.pk}")

        code = data.get("code")
        if not code and pk:
            try:
                code = InstagramIdCodec.encode(pk)
            except Exception:
                code = pk

        caption_text = ""
        if data.get("caption"):
            caption_text = data["caption"].get("text", "")

        taken_at = data.get("taken_at")
        if isinstance(taken_at, int):
            taken_at = datetime.fromtimestamp(taken_at)
        elif not isinstance(taken_at, datetime):
            taken_at = datetime.now()

        video_url = None
        if data.get("video_versions"):
            try:
                video_url = max(data["video_versions"], key=lambda v: v.get("height", 0) * v.get("width", 0)).get("url")
            except Exception:
                pass

        thumbnail_url = None
        candidates = (data.get("image_versions2") or {}).get("candidates", [])
        if candidates:
            try:
                thumbnail_url = max(candidates, key=lambda c: c.get("height", 0) * c.get("width", 0)).get("url")
            except Exception:
                pass

        media_type = data.get("media_type", 1)
        resources = []
        if media_type == 8:
            for item in data.get("carousel_media", []):
                try:
                    resources.append(SimpleResource.from_dict(item))
                except Exception:
                    continue

        return cls(
            pk=pk,
            id=media_id,
            code=code or pk,
            media_type=media_type,
            caption_text=caption_text,
            user=user,
            taken_at=taken_at,
            video_url=video_url,
            thumbnail_url=thumbnail_url,
            resources=resources,
            product_type=data.get("product_type") or "feed",
        )
