"""Reading an account off the user object every tweet payload embeds.

X has been moving fields out of `legacy` into their own objects (core, avatar, location,
privacy, verification), and payloads from different endpoints sit at different points of that
move, so each field is read from where it lives now with `legacy` as the fallback.
"""

from email.utils import parsedate_to_datetime
from typing import Any

from social_archiver.core.profiles import ImageRef, ProfileSnapshot, ProfileStatus

# `_normal` is a 48px thumbnail; the same picture is served at 400x400 under another suffix
_AVATAR_SIZE = ("_normal.", "_400x400.")
_BANNER_SIZE = "/1500x500"


def _expand_links(text: str, entities: dict[str, Any]) -> str:
    """A bio stores its links as t.co redirects; the entities say where each one goes."""
    for link in entities.get("urls", []):
        if link.get("url") and link.get("expanded_url"):
            text = text.replace(link["url"], link["expanded_url"])
    return text


def profile_snapshot(user: dict[str, Any]) -> ProfileSnapshot | None:
    """None for a user object that names no account, which an unavailable tweet's can be."""
    profile_id = user.get("rest_id")
    legacy = user.get("legacy", {})
    core = user.get("core", {})
    handle = core.get("screen_name") or legacy.get("screen_name")
    if not profile_id or not handle:
        return None

    avatar_url = user.get("avatar", {}).get("image_url") or legacy.get("profile_image_url_https")
    banner_url = legacy.get("profile_banner_url")
    joined = core.get("created_at") or legacy.get("created_at")
    website = next(iter(legacy.get("entities", {}).get("url", {}).get("urls", [])), {}).get("expanded_url")

    return ProfileSnapshot(
        profile_id=profile_id,
        handle=handle,
        display_name=core.get("name") or legacy.get("name"),
        bio=_expand_links(legacy.get("description", ""), legacy.get("entities", {}).get("description", {})),
        location=user.get("location", {}).get("location", legacy.get("location", "")),
        website=website or "",
        verified=bool(user.get("is_blue_verified") or user.get("verification", {}).get("verified")),
        private=bool(user.get("privacy", {}).get("protected", legacy.get("protected"))),
        avatar=ImageRef(avatar_url, avatar_url.replace(*_AVATAR_SIZE)) if avatar_url else None,
        banner=ImageRef(banner_url, banner_url + _BANNER_SIZE) if banner_url else None,
        followers=legacy.get("followers_count"),
        following=legacy.get("friends_count"),
        posts=legacy.get("statuses_count"),
        joined_at=parsedate_to_datetime(joined) if joined else None,
        status=ProfileStatus.ACTIVE,
    )
