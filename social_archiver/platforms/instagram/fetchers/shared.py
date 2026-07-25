import logging
from collections.abc import Iterator
from datetime import datetime
from urllib.parse import urlsplit

from instagrapi.utils import InstagramIdCodec

from social_archiver.platforms.instagram import config
from social_archiver.platforms.instagram.client import InstagramClient
from social_archiver.platforms.instagram.fetchers.page import MediaPage
from social_archiver.platforms.instagram.simple_media import SimpleMedia, SimpleUser

logger = logging.getLogger(__name__)

THREAD_PAGE_SIZE = 100  # the server caps a thread page near 75 whatever is asked for
MEDIA_INFO_BATCH = 50
MAX_MEDIA_PK = 2**64  # a media pk is a 64-bit int; anything larger is not a shortcode
SEQ_ID = "40065"  # instagrapi sends this fixed value on every thread request

XMA_KEYS = ("xma_clip", "xma_media_share")

# Message types that never carry archivable media, so an unhandled one is worth a warning
TEXT_ITEM_TYPES = frozenset(
    {"text", "like", "action_log", "placeholder", "link", "animated_media", "voice_media", "video_call_event"}
)


class SharedFetcher:
    """Yields the media shared in a DM thread, one API page at a time.

    A share carries a preview thumbnail and a link, never the post itself, so the real
    media has to be resolved by id. `media/infos/` resolves a whole page of them in one
    request; instagrapi only exposes `media/{pk}/info/`, which would be a request each."""

    def __init__(self, ig_client: InstagramClient):
        self.ig_client = ig_client
        self._thread_id: int | None = None
        self._usernames: dict[str, str] = {}

    def fetch_shared_media(self, dm_username: str, amount: int = 0, start_cursor: str = "") -> Iterator[MediaPage]:
        logger.info(f"Fetching shared media from DM with {dm_username} (amount={amount if amount > 0 else 'all'})")
        requests_before = self.ig_client.request_count

        if self._thread_id is None:
            self._thread_id = self._get_thread_id(dm_username)

        cursor = start_cursor
        if cursor:
            logger.info("Resuming the thread walk from a stored cursor rather than the top")
        fetched = 0
        while amount == 0 or fetched < amount:
            media, cursor, raw_count = self._fetch_thread_chunk(cursor)
            if amount > 0:
                media = media[: amount - fetched]
            fetched += len(media)
            if media or raw_count:
                yield MediaPage(media, cursor, raw_count)
            if not cursor:
                break

        logger.info(f"Fetched {fetched} shared media in {self.ig_client.request_count - requests_before} requests")

    def _fetch_thread_chunk(self, cursor: str) -> tuple[list[SimpleMedia], str, int]:
        params = {
            "visual_message_return_type": "unseen",
            "direction": "older",
            "seq_id": SEQ_ID,
            "limit": str(THREAD_PAGE_SIZE),
        }
        if cursor:
            params["cursor"] = cursor

        thread = self.ig_client.private_request(f"direct_v2/threads/{self._thread_id}/", params)["thread"]
        self._remember_usernames(thread)

        media: list[SimpleMedia] = []
        senders: dict[str, str | None] = {}  # media pk -> who shared it into the thread
        for item in thread.get("items", []):
            sender = self._usernames.get(str(item.get("user_id")))
            if embedded := _embedded_media(item):
                media.append(self._build(embedded, sender))
            elif pk := _shared_media_pk(item):
                senders[pk] = sender
            elif preview := _preview_media(item, sender):
                media.append(preview)
            elif any(item.get(key) for key in XMA_KEYS):
                logger.info(f"Share not archived ({_skip_reason(item)}), message {item.get('item_id')}")
            elif item.get("item_type") not in TEXT_ITEM_TYPES:
                logger.warning(f"Unhandled message type {item.get('item_type')!r}, any media in it not archived")

        media.extend(self._resolve(list(senders), senders))
        return [m for m in media if m], thread.get("oldest_cursor", ""), len(thread.get("items", []))

    def _resolve(self, pks: list[str], senders: dict[str, str | None]) -> list[SimpleMedia]:
        """Resolve shared posts by id, MEDIA_INFO_BATCH per request. Media the account can
        no longer see (deleted, or the account went private) is absent from the response."""
        resolved: list[SimpleMedia] = []
        for start in range(0, len(pks), MEDIA_INFO_BATCH):
            chunk = pks[start : start + MEDIA_INFO_BATCH]
            items = self._resolve_chunk(chunk)
            if len(items) < len(chunk):
                logger.warning(f"{len(chunk) - len(items)} of {len(chunk)} shared posts no longer available")
            resolved.extend(self._build(raw, senders.get(str(raw.get("pk")))) for raw in items)
        return [m for m in resolved if m]

    def _resolve_chunk(self, chunk: list[str]) -> list[dict]:
        """Instagram answers a batch holding one unresolvable id with an empty response for
        the *whole* batch, so a single bad id silently costs every id sent with it. Splitting
        on an empty response isolates the bad one instead of losing the batch, and costs the
        extra requests only when there is one."""
        items = self.ig_client.private_request("media/infos/", {"media_ids": ",".join(chunk)}).get("items", [])
        if items:
            return items
        if len(chunk) == 1:
            logger.warning(f"Shared post {chunk[0]} could not be resolved, not archived")
            return []
        mid = len(chunk) // 2
        return self._resolve_chunk(chunk[:mid]) + self._resolve_chunk(chunk[mid:])

    def _build(self, raw: dict, sender: str | None) -> SimpleMedia | None:
        try:
            media = SimpleMedia.from_dict(raw)
        except Exception as e:
            logger.warning(f"Failed to extract shared media {raw.get('pk', 'unknown')}: {e}")
            return None
        media.shared_by_username = sender
        return media

    def _remember_usernames(self, thread: dict):
        """The thread names its own participants, so attributing a share to whoever sent it
        costs nothing — resolving each sender individually would be a request per message."""
        if self._usernames:
            return
        self._usernames = {str(user["pk"]): user["username"] for user in thread.get("users", [])}
        self._usernames[str(thread["viewer_id"])] = config.INSTAGRAM_USERNAME

    def _get_thread_id(self, username: str) -> int:
        user_id = self.ig_client.get_user_id_by_username(username)
        result = self.ig_client.client.direct_thread_by_participants([int(user_id)])

        thread_data = result.get("thread", {})
        thread_id = thread_data.get("thread_id") or thread_data.get("thread_v2_id")
        if not thread_id:
            raise ValueError(f"Could not find thread_id in response for user {username}")

        logger.info(f"Found thread ID {thread_id} for user {username}")
        return int(thread_id)


def _embedded_media(item: dict) -> dict | None:
    """A legacy share embeds the whole media; an xma one carries only a link to it."""
    for candidate in (item.get("media_share"), item.get("media")):
        if isinstance(candidate, dict) and candidate.get("pk"):
            return candidate
    if isinstance(clip := item.get("clip"), dict):
        inner = clip.get("clip", clip)
        if inner.get("pk"):
            return inner
    for key in ("reel_share", "story_share"):
        if isinstance(share := item.get(key), dict) and isinstance(share.get("media"), dict):
            return share["media"]
    return None


def _skip_reason(item: dict) -> str:
    """Why a share yielded nothing, so the log distinguishes a post that is simply gone from
    a link shape the extractor does not handle — only the second is worth code."""
    for key in XMA_KEYS:
        if not (value := item.get(key)):
            continue
        payload = value[0]
        target = payload.get("target_url") or ""
        preview = "preview=no" if not payload.get("preview_url") else "preview=yes"
        if not target:
            return f"no link, {preview}"
        parts = urlsplit(target).path.strip("/").split("/")
        if len(parts) < 2 or parts[0] not in ("p", "reel", "tv"):
            return f"link path {'/'.join(parts[:2])!r} unrecognised, {preview}"
        if InstagramIdCodec.decode(parts[1]) >= MAX_MEDIA_PK:
            return f"link is an opaque token not a shortcode, {preview}"
        return f"unknown, {preview}"
    return "not an xma share"


def _preview_media(item: dict, sender: str | None) -> SimpleMedia | None:
    """Fallback for a share that cannot be resolved to its post — one linked by opaque token
    rather than shortcode. Instagram's own preview is then the only copy obtainable, so it is
    archived with the link, typed `xma_preview` to stay distinguishable from a real post.
    Only ever reached after resolution fails, so a preview never displaces real media."""
    for key in XMA_KEYS:
        if not (value := item.get(key)):
            continue
        payload = value[0]
        if not (url := payload.get("preview_url")):
            return None
        target = payload.get("target_url") or ""
        return SimpleMedia(
            pk=f"xma_{item['item_id']}",
            id=str(payload.get("preview_media_fbid") or item["item_id"]),
            code=urlsplit(target).path.strip("/").split("/")[-1] or str(item["item_id"]),
            media_type=1,
            caption_text=payload.get("caption_body_text") or payload.get("title_text") or "",
            user=SimpleUser(pk="", username=payload.get("header_title_text") or "unknown"),
            taken_at=datetime.fromtimestamp(int(item["timestamp"]) / 1e6),
            thumbnail_url=url,
            product_type="xma_preview",
            shared_by_username=sender,
            link_url=target or None,
        )
    return None


def _shared_media_pk(item: dict) -> str | None:
    """An xma share carries a preview and a link, never the post itself. The permalink's
    shortcode decodes to the media id, which is what media/infos/ resolves in bulk.

    Only `xma_clip` puts the id in the query as well; `xma_media_share` has just the code."""
    for key in XMA_KEYS:
        if value := item.get(key):
            parts = urlsplit(value[0].get("target_url") or "").path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in ("p", "reel", "tv"):
                pk = InstagramIdCodec.decode(parts[1])
                # A media pk is a 64-bit int. Some shares link by opaque token rather than
                # shortcode, which decodes to a far larger number that Instagram rejects.
                if pk < MAX_MEDIA_PK:
                    return str(pk)
    return None
