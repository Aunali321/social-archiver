"""Forgiving media types for the Instagram archiver — more lenient than instagrapi's strict Pydantic models."""
from dataclasses import dataclass, field
from datetime import datetime

from instagrapi.types import Media

from social_archiver.core.database import ArchiveStatus, Item, StageStatus


def _url(value: object) -> str | None:
    return str(value) if value else None


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

    @classmethod
    def from_instagrapi(cls, media: Media, shared_by_username: str | None = None) -> "SimpleMedia":
        """Convert an instagrapi model (e.g. a DM media share) so the rest of
        the archiver only ever handles SimpleMedia."""
        return cls(
            pk=str(media.pk),
            id=media.id,
            code=media.code or str(media.pk),
            media_type=media.media_type,
            caption_text=media.caption_text or "",
            user=SimpleUser(pk=str(media.user.pk), username=media.user.username, full_name=media.user.full_name),
            taken_at=media.taken_at,
            video_url=_url(media.video_url),
            thumbnail_url=_url(media.thumbnail_url),
            resources=[
                SimpleResource(
                    pk=str(resource.pk),
                    media_type=resource.media_type,
                    video_url=_url(resource.video_url),
                    thumbnail_url=_url(resource.thumbnail_url),
                )
                for resource in media.resources
            ],
            product_type=media.product_type or "feed",
            shared_by_username=shared_by_username,
        )

    @property
    def media_urls(self) -> list[str]:
        """Best-quality source URL per media file, in album order. These are
        persisted so media can be re-downloaded long after fetching."""
        match self.media_type:
            case 8:
                candidates = (
                    resource.video_url if resource.media_type == 2 else resource.thumbnail_url
                    for resource in self.resources
                )
                return [url for url in candidates if url]
            case 2:
                return [self.video_url] if self.video_url else []
            case _:
                return [self.thumbnail_url] if self.thumbnail_url else []

    @property
    def media_kinds(self) -> list[str]:
        if self.media_type == 8:
            return sorted({"video" if resource.media_type == 2 else "photo" for resource in self.resources})
        return ["video"] if self.media_type == 2 else ["photo"]

    def to_item(self, category: str) -> Item:
        """IGTV is excluded from the archive; everything else awaits the
        download step (Instagram posts always carry media)."""
        if self.product_type == "igtv":
            archive_status, stage_status = ArchiveStatus.SKIPPED, StageStatus.SKIPPED
        else:
            archive_status, stage_status = ArchiveStatus.PENDING, StageStatus.PENDING

        return Item(
            item_id=self.pk,
            platform="instagram",
            category=category,
            author_username=self.user.username,
            author_id=self.user.pk,
            text=self.caption_text or None,
            post_url=f"https://instagram.com/p/{self.code}",
            created_at=self.taken_at,
            has_media=True,
            media_count=len(self.resources) if self.media_type == 8 else 1,
            media_types=self.media_kinds,
            media_urls=self.media_urls,
            collection_name=self.collection_name,
            shared_by_username=self.shared_by_username,
            product_type=self.product_type,
            archive_status=archive_status,
            upload_status=stage_status,
            embed_status=stage_status,
        )
