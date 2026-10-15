"""The /api profile endpoints: finding accounts, and one account's whole history.

An account's items are not served here: they are the timeline filtered by `author_id`, so
they page, filter and render exactly as every other list of items does.
"""

import importlib
from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from social_archiver.api.content import AuthorOut, FilterParams, author_out, image_url, profiles, reader
from social_archiver.core.media_kind import guess_mime_type
from social_archiver.core.profiles import ProfileStatus
from social_archiver.read.models import Profile, ProfileVersion

router = APIRouter()


class ProfileHitOut(BaseModel):
    author: AuthorOut
    bio: str | None
    items: int


class VersionOut(BaseModel):
    handle: str
    display_name: str | None
    bio: str | None
    location: str | None
    website: str | None
    verified: bool | None
    private: bool | None
    avatar_url: str | None
    banner_url: str | None
    first_seen: datetime
    last_seen: datetime


class EncountersOut(BaseModel):
    total: int
    origins: dict[str, int]
    first: datetime | None
    last: datetime | None


class ProfileOut(BaseModel):
    platform: str
    profile_id: str
    url: str
    status: ProfileStatus
    is_self: bool
    followers: int | None
    following: int | None
    posts: int | None
    joined_at: datetime | None
    first_seen: datetime
    last_seen: datetime
    observed_at: datetime | None
    versions: list[VersionOut]  # newest first; the first is the account as it is now
    encounters: EncountersOut


def _version_out(platform: str, version: ProfileVersion) -> VersionOut:
    return VersionOut(
        handle=version.handle,
        display_name=version.display_name,
        bio=version.bio,
        location=version.location,
        website=version.website,
        verified=version.verified,
        private=version.private,
        avatar_url=image_url(platform, version.avatar),
        banner_url=image_url(platform, version.banner),
        first_seen=version.first_seen,
        last_seen=version.last_seen,
    )


def _profile_out(profile: Profile) -> ProfileOut:
    port = importlib.import_module(f"social_archiver.platforms.{profile.platform}.service").PORT
    return ProfileOut(
        platform=profile.platform,
        profile_id=profile.profile_id,
        url=port.profile_url(profile.current.handle),
        status=profile.status,
        is_self=profile.is_self,
        followers=profile.followers,
        following=profile.following,
        posts=profile.posts,
        joined_at=profile.joined_at,
        first_seen=profile.first_seen,
        last_seen=profile.last_seen,
        observed_at=profile.observed_at,
        versions=[_version_out(profile.platform, version) for version in profile.versions],
        encounters=EncountersOut(**asdict(profile.encounters)),
    )


@router.get("/api/profiles", response_model=list[ProfileHitOut])
async def search_profiles(
    q: str = "", platforms: str | None = None, limit: int = Query(20, ge=1, le=100)
) -> list[ProfileHitOut]:
    """Accounts matching every word of `q` in any handle, name or bio they have had, most
    archived first; without `q`, the most archived accounts."""
    selected = FilterParams(platforms=platforms).filters().platforms or tuple(await reader.present())
    return [
        ProfileHitOut(author=author_out(hit.author), bio=hit.bio, items=hit.items)
        for hit in await profiles.search(q, selected, limit)
    ]


@router.get("/api/profiles/{platform}/{profile_id}", response_model=ProfileOut)
async def get_profile(platform: str, profile_id: str) -> ProfileOut:
    profile = await profiles.get(platform, profile_id)
    if profile is None:
        raise HTTPException(404, f"no {platform} profile {profile_id}")
    return _profile_out(profile)


@router.get("/api/profile-images/{platform}/{image_id}")
async def profile_image(platform: str, image_id: int) -> FileResponse:
    path = await profiles.image(platform, image_id)
    if path is None or not path.exists():
        raise HTTPException(404, f"no {platform} profile picture {image_id}")
    # Immutable: an id names one picture forever, so the browser never needs to ask again
    return FileResponse(
        path, media_type=guess_mime_type(path), headers={"Cache-Control": "private, max-age=31536000, immutable"}
    )
