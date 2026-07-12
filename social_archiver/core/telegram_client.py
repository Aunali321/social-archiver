import asyncio
import io
import logging
from datetime import datetime
from pathlib import Path

from telegram import Bot, InputMediaPhoto, InputMediaVideo
from telegram.error import RetryAfter, TelegramError, TimedOut

from social_archiver.core import config
from social_archiver.core.media_kind import is_video_file

logger = logging.getLogger(__name__)

MAX_CAPTION_LENGTH = 1024
MAX_MEDIA_GROUP_SIZE = 10


class FileTooLargeError(Exception):
    pass


class TelegramClient:
    def __init__(self):
        if config.TELEGRAM_BOT_API_URL:
            self.bot = Bot(token=config.TELEGRAM_BOT_TOKEN, base_url=config.TELEGRAM_BOT_API_URL)
            logger.info(f"Using custom Telegram Bot API server: {config.TELEGRAM_BOT_API_URL}")
        else:
            self.bot = Bot(token=config.TELEGRAM_BOT_TOKEN)

    def _check_file_size(self, file_path: Path) -> None:
        size_mb = file_path.stat().st_size / (1024 * 1024)
        if size_mb > config.TELEGRAM_MAX_FILE_SIZE_MB:
            raise FileTooLargeError(
                f"File {file_path.name} is {size_mb:.2f} MB (exceeds {config.TELEGRAM_MAX_FILE_SIZE_MB} MB limit)"
            )

    def _check_media_group_size(self, file_paths: list[Path]) -> None:
        total_mb = sum(fp.stat().st_size for fp in file_paths) / (1024 * 1024)
        if total_mb > config.TELEGRAM_MAX_FILE_SIZE_MB:
            raise FileTooLargeError(
                f"Media group total size is {total_mb:.2f} MB (exceeds {config.TELEGRAM_MAX_FILE_SIZE_MB} MB limit)"
            )

    async def send_media(
        self, chat_id: int, file_paths: list[Path], caption: str, max_retries: int = 3
    ) -> list[int]:
        if len(file_paths) == 1:
            self._check_file_size(file_paths[0])
        else:
            for file_path in file_paths:
                self._check_file_size(file_path)
            self._check_media_group_size(file_paths)

        for attempt in range(1, max_retries + 1):
            try:
                if len(file_paths) == 1:
                    return await self._send_single_media(chat_id, file_paths[0], caption)
                if len(file_paths) <= MAX_MEDIA_GROUP_SIZE:
                    return await self._send_media_group(chat_id, file_paths, caption)
                return await self._send_large_album(chat_id, file_paths, caption)
            except TimedOut:
                if attempt == max_retries:
                    raise
                wait_time = 5 * attempt
                logger.warning(f"Telegram timeout (attempt {attempt}/{max_retries}), retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            except RetryAfter as e:
                wait_time = e.retry_after + 1
                logger.warning(f"Flood control exceeded. Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)
                if attempt >= max_retries:
                    raise
            except TelegramError as e:
                logger.error(f"Failed to send media to {chat_id}: {e}")
                raise

        raise RuntimeError(f"Failed to send media after {max_retries} attempts")

    async def send_text(self, chat_id: int, text: str, max_retries: int = 3) -> list[int]:
        for attempt in range(1, max_retries + 1):
            try:
                msg = await self.bot.send_message(chat_id=chat_id, text=text[:4096], disable_web_page_preview=False)
                return [msg.message_id]
            except TimedOut:
                if attempt == max_retries:
                    raise
                await asyncio.sleep(5 * attempt)
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                if attempt >= max_retries:
                    raise

        raise RuntimeError("Failed to send text message")

    async def _send_single_media(self, chat_id: int, file_path: Path, caption: str) -> list[int]:
        truncated = self._truncate(caption)

        with open(file_path, "rb") as f:
            send = self.bot.send_video if is_video_file(file_path) else self.bot.send_photo
            kwarg = "video" if is_video_file(file_path) else "photo"
            msg = await send(chat_id=chat_id, **{kwarg: f}, caption=truncated, read_timeout=60, write_timeout=60)

        if len(caption) > MAX_CAPTION_LENGTH:
            await self._send_full_caption(chat_id, caption)

        return [msg.message_id]

    async def _send_media_group(self, chat_id: int, file_paths: list[Path], caption: str) -> list[int]:
        truncated = self._truncate(caption)

        media_group = []
        for idx, file_path in enumerate(file_paths):
            with open(file_path, "rb") as f:
                data = f.read()
            item_caption = truncated if idx == 0 else None
            if is_video_file(file_path):
                media_group.append(InputMediaVideo(media=data, caption=item_caption))
            else:
                media_group.append(InputMediaPhoto(media=data, caption=item_caption))

        messages = await self.bot.send_media_group(chat_id=chat_id, media=media_group, read_timeout=120, write_timeout=120)

        if len(caption) > MAX_CAPTION_LENGTH:
            await self._send_full_caption(chat_id, caption)

        return [msg.message_id for msg in messages]

    async def _send_large_album(self, chat_id: int, file_paths: list[Path], caption: str) -> list[int]:
        logger.info(f"Album has {len(file_paths)} items, splitting into groups of {MAX_MEDIA_GROUP_SIZE}")

        all_message_ids = []
        total_chunks = (len(file_paths) + MAX_MEDIA_GROUP_SIZE - 1) // MAX_MEDIA_GROUP_SIZE

        for i in range(0, len(file_paths), MAX_MEDIA_GROUP_SIZE):
            chunk = file_paths[i : i + MAX_MEDIA_GROUP_SIZE]
            chunk_num = (i // MAX_MEDIA_GROUP_SIZE) + 1
            chunk_caption = (
                f"{caption}\n\nPart {chunk_num}/{total_chunks}" if i == 0 else f"Part {chunk_num}/{total_chunks} (continued)"
            )
            all_message_ids.extend(await self._send_media_group(chat_id, chunk, chunk_caption))
            if i + MAX_MEDIA_GROUP_SIZE < len(file_paths):
                await asyncio.sleep(1)

        return all_message_ids

    async def _send_full_caption(self, chat_id: int, caption: str) -> None:
        caption_file = io.BytesIO(caption.encode("utf-8"))
        caption_file.name = "caption.txt"
        await self.bot.send_document(chat_id=chat_id, document=caption_file, filename="caption.txt", caption="Full caption")

    def _truncate(self, caption: str) -> str:
        if len(caption) <= MAX_CAPTION_LENGTH:
            return caption
        return caption[: MAX_CAPTION_LENGTH - 50] + "\n\n... (caption too long, see next message)"

    async def send_error_notification(self, error_type: str, context: str, traceback: str) -> None:
        if not config.TELEGRAM_CHAT_ERRORS:
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"Error in {context}\n\nType: {error_type}\nTime: {timestamp}\n\nTraceback:\n{traceback}"

        try:
            await self.bot.send_message(chat_id=config.TELEGRAM_CHAT_ERRORS, text=message[:4096])
        except TelegramError as e:
            logger.error(f"Failed to send error notification: {e}")
