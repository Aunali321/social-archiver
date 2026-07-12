import logging
import asyncio
import io
from pathlib import Path
from typing import List, Optional
from datetime import datetime
from telegram import Bot, InputMediaPhoto, InputMediaVideo
from telegram.error import TelegramError, TimedOut, RetryAfter
from twitter_archiver import config

logger = logging.getLogger(__name__)

MAX_CAPTION_LENGTH = 1024
MAX_MEDIA_GROUP_SIZE = 10


class FileTooLargeError(Exception):
    pass


class TelegramClient:
    def __init__(self):
        if config.TELEGRAM_BOT_API_URL:
            self.bot = Bot(
                token=config.TELEGRAM_BOT_TOKEN, base_url=config.TELEGRAM_BOT_API_URL
            )
            logger.info(
                f"Using custom Telegram Bot API server: {config.TELEGRAM_BOT_API_URL}"
            )
        else:
            self.bot = Bot(token=config.TELEGRAM_BOT_TOKEN)

    def _check_file_size(self, file_path: Path) -> None:
        file_size_bytes = file_path.stat().st_size
        file_size_mb = file_size_bytes / (1024 * 1024)
        max_size_mb = config.TELEGRAM_MAX_FILE_SIZE_MB

        if file_size_mb > max_size_mb:
            raise FileTooLargeError(
                f"File {file_path.name} is {file_size_mb:.2f} MB (exceeds {max_size_mb} MB limit)"
            )

    def _check_media_group_size(self, file_paths: List[Path]) -> None:
        total_size_bytes = sum(fp.stat().st_size for fp in file_paths)
        total_size_mb = total_size_bytes / (1024 * 1024)
        max_size_mb = config.TELEGRAM_MAX_FILE_SIZE_MB

        if total_size_mb > max_size_mb:
            raise FileTooLargeError(
                f"Media group total size is {total_size_mb:.2f} MB (exceeds {max_size_mb} MB limit)"
            )

    async def send_media(
        self,
        chat_id: int,
        file_paths: List[Path],
        caption: str,
        has_video: bool = False,
        max_retries: int = 3,
    ) -> List[int]:
        try:
            if len(file_paths) == 1:
                self._check_file_size(file_paths[0])
            else:
                for file_path in file_paths:
                    self._check_file_size(file_path)
                self._check_media_group_size(file_paths)
        except FileTooLargeError as e:
            logger.warning(f"Skipping upload: {e}")
            raise

        for attempt in range(1, max_retries + 1):
            try:
                if len(file_paths) == 1:
                    return await self._send_single_media(
                        chat_id, file_paths[0], caption
                    )
                elif len(file_paths) <= MAX_MEDIA_GROUP_SIZE:
                    return await self._send_media_group(chat_id, file_paths, caption)
                else:
                    return await self._send_large_album(chat_id, file_paths, caption)
            except TimedOut as e:
                if attempt == max_retries:
                    raise
                wait_time = 5 * attempt
                logger.warning(
                    f"Telegram timeout (attempt {attempt}/{max_retries}), retrying in {wait_time}s..."
                )
                await asyncio.sleep(wait_time)
            except RetryAfter as e:
                wait_time = e.retry_after + 1
                logger.warning(
                    f"Flood control exceeded. Waiting {wait_time}s before retry..."
                )
                await asyncio.sleep(wait_time)
                if attempt < max_retries:
                    continue
                else:
                    raise
            except TelegramError as e:
                logger.error(f"Failed to send media to {chat_id}: {e}")
                raise

        raise RuntimeError(f"Failed to send media after {max_retries} attempts")

    async def send_text(self, chat_id: int, text: str, max_retries: int = 3) -> List[int]:
        """Send a text-only message (for tweets without media)."""
        for attempt in range(1, max_retries + 1):
            try:
                msg = await self.bot.send_message(
                    chat_id=chat_id,
                    text=text[:4096],
                    disable_web_page_preview=False,
                )
                return [msg.message_id]
            except TimedOut:
                if attempt == max_retries:
                    raise
                await asyncio.sleep(5 * attempt)
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                if attempt >= max_retries:
                    raise
            except TelegramError:
                raise

        raise RuntimeError("Failed to send text message")

    async def _send_single_media(
        self, chat_id: int, file_path: Path, caption: str
    ) -> List[int]:
        is_video = file_path.suffix.lower() in [".mp4", ".mov", ".avi"]

        if len(caption) > MAX_CAPTION_LENGTH:
            truncated_caption = (
                caption[: MAX_CAPTION_LENGTH - 50]
                + "\n\n... (caption too long, see next message)"
            )
        else:
            truncated_caption = caption

        with open(file_path, "rb") as f:
            if is_video:
                msg = await self.bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    caption=truncated_caption,
                    read_timeout=60,
                    write_timeout=60,
                )
            else:
                msg = await self.bot.send_photo(
                    chat_id=chat_id,
                    photo=f,
                    caption=truncated_caption,
                    read_timeout=60,
                    write_timeout=60,
                )

        if len(caption) > MAX_CAPTION_LENGTH:
            caption_file = io.BytesIO(caption.encode("utf-8"))
            caption_file.name = "caption.txt"
            await self.bot.send_document(
                chat_id=chat_id,
                document=caption_file,
                filename="caption.txt",
                caption="Full caption",
            )

        return [msg.message_id]

    async def _send_media_group(
        self, chat_id: int, file_paths: List[Path], caption: str
    ) -> List[int]:
        if len(caption) > MAX_CAPTION_LENGTH:
            truncated_caption = (
                caption[: MAX_CAPTION_LENGTH - 50]
                + "\n\n... (caption too long, see next message)"
            )
        else:
            truncated_caption = caption

        media_group = []
        for idx, file_path in enumerate(file_paths):
            with open(file_path, "rb") as f:
                file_ext = file_path.suffix.lower()
                is_video = file_ext in [".mp4", ".mov", ".avi"]

                if is_video:
                    media_group.append(
                        InputMediaVideo(
                            media=f.read(),
                            caption=truncated_caption if idx == 0 else None,
                        )
                    )
                else:
                    media_group.append(
                        InputMediaPhoto(
                            media=f.read(),
                            caption=truncated_caption if idx == 0 else None,
                        )
                    )

        messages = await self.bot.send_media_group(
            chat_id=chat_id, media=media_group, read_timeout=120, write_timeout=120
        )

        if len(caption) > MAX_CAPTION_LENGTH:
            caption_file = io.BytesIO(caption.encode("utf-8"))
            caption_file.name = "caption.txt"
            await self.bot.send_document(
                chat_id=chat_id,
                document=caption_file,
                filename="caption.txt",
                caption="Full caption",
            )

        return [msg.message_id for msg in messages]

    async def _send_large_album(
        self, chat_id: int, file_paths: List[Path], caption: str
    ) -> List[int]:
        logger.info(
            f"Album has {len(file_paths)} items, splitting into groups of {MAX_MEDIA_GROUP_SIZE}"
        )

        all_message_ids = []

        for i in range(0, len(file_paths), MAX_MEDIA_GROUP_SIZE):
            chunk = file_paths[i : i + MAX_MEDIA_GROUP_SIZE]
            chunk_num = (i // MAX_MEDIA_GROUP_SIZE) + 1
            total_chunks = (
                len(file_paths) + MAX_MEDIA_GROUP_SIZE - 1
            ) // MAX_MEDIA_GROUP_SIZE

            if i == 0:
                chunk_caption = f"{caption}\n\nPart {chunk_num}/{total_chunks}"
            else:
                chunk_caption = f"Part {chunk_num}/{total_chunks} (continued)"

            message_ids = await self._send_media_group(chat_id, chunk, chunk_caption)
            all_message_ids.extend(message_ids)

            if i + MAX_MEDIA_GROUP_SIZE < len(file_paths):
                await asyncio.sleep(1)

        return all_message_ids

    async def send_error_notification(
        self, error_type: str, context: str, traceback: str
    ):
        if not config.TELEGRAM_CHAT_ERRORS:
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""Error in {context}

Type: {error_type}
Time: {timestamp}

Traceback:
{traceback}
"""

        try:
            await self.bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ERRORS, text=message[:4096]
            )
        except TelegramError as e:
            logger.error(f"Failed to send error notification: {e}")

    def format_caption(
        self,
        tweet_text: Optional[str],
        author_username: str,
        tweet_id: str,
        created_at: Optional[datetime],
        like_count: Optional[int] = None,
        retweet_count: Optional[int] = None,
        origin: Optional[str] = None,
    ) -> str:
        caption_parts = []

        if tweet_text:
            caption_parts.append(tweet_text)

        if origin and origin != "liked":
            origin_labels = {
                "thread": "thread",
                "parent": "parent",
                "quoted": "quoted",
                "liked_reply": "liked reply",
                "retweet": "retweet",
            }
            label = origin_labels.get(origin, origin)
            caption_parts.append(f"\n[{label}]")

        caption_parts.append(f"@{author_username}")

        stats = []
        if like_count is not None:
            stats.append(f"{like_count} likes")
        if retweet_count is not None:
            stats.append(f"{retweet_count} RTs")
        if stats:
            caption_parts.append(" | ".join(stats))

        caption_parts.append(f"https://x.com/{author_username}/status/{tweet_id}")

        if created_at:
            caption_parts.append(f"{created_at.strftime('%Y-%m-%d %H:%M:%S')}")

        return "\n".join(caption_parts)
