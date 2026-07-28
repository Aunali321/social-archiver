"""asyncpraw wrapper: authenticated Reddit access, batch hydration of fullnames,
and live listing top-up. All of PRAW's dynamic-attribute and raw-JSON access is
confined here, converting objects into the typed `RedditItem` model."""

import html
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from urllib.parse import urlsplit

import asyncpraw
from asyncpraw.models import Comment, Submission

from social_archiver.core.downloader import is_direct_media
from social_archiver.platforms.reddit import config
from social_archiver.platforms.reddit.simple_post import RedditItem, RedditMedia

logger = logging.getLogger(__name__)

INFO_BATCH = 100

_EXTERNAL_VIDEO_HOSTS = (
    "streamable.com",
    "redgifs.com",
    "v.redd.it",
)

_YOUTUBE_HOSTS = ("youtube.com", "youtu.be")


class RedditClient:
    def __init__(self):
        # client_secret is None for an installed app, where the refresh token carries auth
        kwargs = {
            "client_id": config.REDDIT_CLIENT_ID,
            "client_secret": config.REDDIT_CLIENT_SECRET or None,
            "user_agent": config.REDDIT_USER_AGENT,
        }
        if config.REDDIT_REFRESH_TOKEN:
            kwargs["refresh_token"] = config.REDDIT_REFRESH_TOKEN
        else:
            kwargs["username"] = config.REDDIT_USERNAME
            kwargs["password"] = config.REDDIT_PASSWORD
        self._reddit = asyncpraw.Reddit(**kwargs)

    async def verify(self) -> str:
        me = await self._reddit.user.me()
        if me is None:
            raise RuntimeError("Reddit authentication returned no user (read-only credentials?)")
        return me.name

    async def hydrate(self, fullnames: list[str]) -> list[RedditItem]:
        """Fetch full submissions/comments for the given fullnames, 100 per request.
        Fullnames Reddit can no longer resolve (deleted) are simply absent."""
        items: list[RedditItem] = []
        for start in range(0, len(fullnames), INFO_BATCH):
            chunk = fullnames[start : start + INFO_BATCH]
            async for obj in self._reddit.info(fullnames=chunk):
                items.append(_parse(obj))
        return items

    async def listing(self, category: str, limit: int | None) -> AsyncIterator[RedditItem]:
        me = await self._reddit.user.me()
        source = {
            "saved": me.saved,
            "upvoted": me.upvoted,
            "downvoted": me.downvoted,
            "own": me.new,
        }[category]
        async for obj in source(limit=limit):
            yield _parse(obj)

    async def close(self):
        await self._reddit.close()


def _parse(obj: Submission | Comment) -> RedditItem:
    if isinstance(obj, Submission):
        return _parse_submission(obj)
    if isinstance(obj, Comment):
        return _parse_comment(obj)
    raise TypeError(f"Unexpected Reddit object: {type(obj).__name__}")


def _parse_submission(s: Submission) -> RedditItem:
    return RedditItem(
        fullname=s.fullname,
        kind="post",
        author=str(s.author) if s.author else "[deleted]",
        subreddit=str(s.subreddit),
        permalink=f"https://reddit.com{s.permalink}",
        created_at=datetime.fromtimestamp(s.created_utc, tz=timezone.utc),
        score=s.score,
        title=s.title,
        body=s.selftext or None,
        num_comments=s.num_comments,
        over_18=s.over_18,
        link_flair=s.link_flair_text,
        submission_fullname=s.fullname,
        # A self post's url is its own permalink; only a link post points elsewhere
        link_url=None if s.is_self else (s.url or None),
        media=_submission_media(s),
    )


def _parse_comment(c: Comment) -> RedditItem:
    subreddit = str(c.subreddit)
    return RedditItem(
        fullname=c.fullname,
        kind="comment",
        author=str(c.author) if c.author else "[deleted]",
        subreddit=subreddit,
        permalink=f"https://reddit.com/r/{subreddit}/comments/{c.link_id[3:]}/_/{c.id}/",
        created_at=datetime.fromtimestamp(c.created_utc, tz=timezone.utc),
        score=c.score,
        body=c.body,
        submission_fullname=c.link_id,
        parent_fullname=c.parent_id,
        over_18=getattr(c, "over_18", False),
        media=_metadata_media(getattr(c, "media_metadata", None)),
    )


class _RawSubmission:
    """A crosspost's parent arrives as raw JSON inside the child, not as a Submission.
    Wrapping it lets the same extractor read either one."""

    def __init__(self, data: dict):
        self._data = data

    def __getattr__(self, name: str):
        return self._data.get(name)


def raw_media(data: dict, kind: str) -> list[RedditMedia]:
    """Media for a record that arrived as raw API JSON rather than through praw, which is the
    shape an archive dump stores. Same extractors as the live path, so a post ingested from a
    dump resolves to the same urls as the same post fetched from Reddit."""
    return _submission_media(_RawSubmission(data)) if kind == "post" else _metadata_media(data.get("media_metadata"))


def _submission_media(s: Submission) -> list[RedditMedia]:
    """Media in priority order: what the post itself carries, then what it crossposted,
    then the link preview Reddit generated. The preview is never user-uploaded, so it
    only stands in when the post has nothing of its own."""
    if media := _own_media(s):
        return media

    if parents := getattr(s, "crosspost_parent_list", None):
        if media := _own_media(_RawSubmission(parents[0])):
            return media

    return _preview_media(getattr(s, "preview", None))


def _own_media(s: Submission | _RawSubmission) -> list[RedditMedia]:
    if getattr(s, "is_gallery", False):
        if media := _metadata_media(getattr(s, "media_metadata", None), _gallery_order(s)):
            return media
    if getattr(s, "is_video", False):
        # DASH video/audio are separate tracks; yt-dlp muxes them from the permalink
        return [RedditMedia("video", f"https://reddit.com{s.permalink}")]
    # Images embedded in the body of a text post, which carries no url of its own
    if media := _metadata_media(getattr(s, "media_metadata", None)):
        return media

    url = (getattr(s, "url", "") or "").strip()
    if not url or getattr(s, "is_self", False):
        return []

    hint = getattr(s, "post_hint", "") or ""
    if hint == "image" or is_direct_media(url):
        return [RedditMedia("gif" if url.lower().endswith(".gif") else "image", url)]
    if _is_youtube(url):
        return []
    if hint in ("rich:video", "hosted:video") or _is_external_video(url):
        return [RedditMedia("video", url)]
    return []


def _metadata_media(meta: dict | None, order: list[str] | None = None) -> list[RedditMedia]:
    """media_metadata is raw API JSON keyed by media id, carrying both gallery items and
    the images embedded inline in a post or comment body. i.redd.it serves the original
    upload; the urls inside the entry are resized renditions."""
    meta = meta or {}
    out: list[RedditMedia] = []
    for media_id in order or list(meta):
        entry = meta.get(media_id)
        if not entry or entry.get("status") != "valid":
            continue

        source = entry.get("s") or {}
        match entry.get("e"):
            case "Image":
                # i.redd.it serves the file under the mime subtype verbatim: image/jpeg is
                # `.jpeg`, and rewriting it to `.jpg` 404s.
                ext = entry.get("m", "image/jpeg").split("/")[-1]
                out.append(RedditMedia("image", f"https://i.redd.it/{media_id}.{ext}"))
            case "AnimatedImage":
                if url := html.unescape(source.get("mp4") or source.get("gif") or ""):
                    out.append(RedditMedia("gif", url))
            case "RedditVideo":
                if url := html.unescape(source.get("dashUrl") or source.get("hlsUrl") or ""):
                    out.append(RedditMedia("video", url))
            case unknown:
                logger.warning(f"Unhandled media_metadata type {unknown!r} for {media_id}, media not archived")
    return out


def _gallery_order(s: Submission | _RawSubmission) -> list[str] | None:
    data: dict = getattr(s, "gallery_data", None) or {}
    return [item["media_id"] for item in data.get("items", [])] or None


def _preview_media(preview: dict | None) -> list[RedditMedia]:
    """The thumbnail Reddit renders for a link, the only image a link-only post has."""
    out: list[RedditMedia] = []
    for image in (preview or {}).get("images", []):
        variants = image.get("variants") or {}
        best = variants.get("mp4") or variants.get("gif") or image
        if url := html.unescape((best.get("source") or {}).get("url", "")):
            out.append(RedditMedia("preview", url))
    return out


def _is_youtube(url: str) -> bool:
    """A link post to YouTube resolves to the full source video at best available quality,
    which is the linked platform's content rather than the Reddit post. The url and Reddit's
    own preview are still archived; the video itself is not."""
    host = urlsplit(url.lower()).netloc
    return any(host == known or host.endswith(f".{known}") for known in _YOUTUBE_HOSTS)


def _is_external_video(url: str) -> bool:
    lowered = url.lower()
    if lowered.endswith(".gifv"):
        return True
    host = urlsplit(lowered).netloc
    return any(video_host in host for video_host in _EXTERNAL_VIDEO_HOSTS)
