"""
Custom simplified media types for Instagram archiver.
These types are more forgiving than instagrapi's strict Pydantic models.
"""
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime


@dataclass
class SimpleUser:
    """Simplified user information"""
    pk: str
    username: str
    full_name: Optional[str] = None
    
    @classmethod
    def from_dict(cls, data: dict) -> "SimpleUser":
        """Extract user from API response, handling missing fields gracefully"""
        return cls(
            pk=str(data.get("pk", data.get("id", ""))),
            username=data.get("username", "unknown"),
            full_name=data.get("full_name")
        )


@dataclass
class SimpleResource:
    """Simplified resource (for carousel/album items)"""
    pk: str
    media_type: int  # 1=photo, 2=video
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    
    @classmethod
    def from_dict(cls, data: dict) -> "SimpleResource":
        """Extract resource from API response"""
        # Find best quality video
        video_url = None
        if "video_versions" in data and data["video_versions"]:
            video_url = sorted(
                data["video_versions"],
                key=lambda v: v.get("height", 0) * v.get("width", 0)
            )[-1].get("url")
        
        # Find best quality thumbnail
        thumbnail_url = None
        if "image_versions2" in data and data["image_versions2"]:
            candidates = data["image_versions2"].get("candidates", [])
            if candidates:
                thumbnail_url = sorted(
                    candidates,
                    key=lambda c: c.get("height", 0) * c.get("width", 0)
                )[-1].get("url")
        
        return cls(
            pk=str(data.get("pk", data.get("id", ""))),
            media_type=data.get("media_type", 1),
            video_url=video_url,
            thumbnail_url=thumbnail_url
        )


@dataclass
class SimpleMedia:
    """
    Simplified media type with only fields needed for download/upload.
    Much more forgiving than instagrapi's strict Pydantic models.
    """
    pk: str  # Media primary key
    id: str  # Full media ID (pk_userid)
    code: str  # Short code for URL
    media_type: int  # 1=photo, 2=video, 8=album
    caption_text: str
    user: SimpleUser
    taken_at: datetime
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    resources: List[SimpleResource] = field(default_factory=list)  # For albums
    product_type: str = "feed"  # feed, clips, igtv, etc.
    collection_name: Optional[str] = None  # For saved posts
    shared_by_username: Optional[str] = None  # For DM shared posts
    
    @classmethod
    def from_dict(cls, data: dict) -> "SimpleMedia":
        """
        Extract media from API response, handling all the problematic None values gracefully.
        This replaces instagrapi's extract_media_v1 which has strict Pydantic validation.
        """
        from instagrapi.utils import InstagramIdCodec
        
        # Extract basic fields
        pk = str(data.get("pk", ""))
        user_data = data.get("user", {})
        user = SimpleUser.from_dict(user_data)
        
        # Build full ID
        media_id = data.get("id", f"{pk}_{user.pk}")
        
        # Get code (shortcode)
        code = data.get("code")
        if not code and pk:
            try:
                code = InstagramIdCodec.encode(pk)
            except:
                code = pk
        
        # Extract caption
        caption_text = ""
        if "caption" in data and data["caption"]:
            caption_text = data["caption"].get("text", "")
        
        # Extract taken_at
        taken_at = data.get("taken_at")
        if isinstance(taken_at, int):
            taken_at = datetime.fromtimestamp(taken_at)
        elif not isinstance(taken_at, datetime):
            taken_at = datetime.now()
        
        # Find best quality video
        video_url = None
        if "video_versions" in data and data["video_versions"]:
            try:
                video_url = sorted(
                    data["video_versions"],
                    key=lambda v: v.get("height", 0) * v.get("width", 0)
                )[-1].get("url")
            except:
                pass
        
        # Find best quality thumbnail
        thumbnail_url = None
        if "image_versions2" in data and data["image_versions2"]:
            try:
                candidates = data["image_versions2"].get("candidates", [])
                if candidates:
                    thumbnail_url = sorted(
                        candidates,
                        key=lambda c: c.get("height", 0) * c.get("width", 0)
                    )[-1].get("url")
            except:
                pass
        
        # Extract resources for albums (media_type 8)
        media_type = data.get("media_type", 1)
        resources = []
        if media_type == 8 and "carousel_media" in data:
            for item in data.get("carousel_media", []):
                try:
                    resources.append(SimpleResource.from_dict(item))
                except:
                    continue
        
        # Get product type
        product_type = data.get("product_type", "feed")
        if media_type == 2 and not product_type:
            product_type = "feed"
        
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
            product_type=product_type
        )
