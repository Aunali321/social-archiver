import logging
import asyncio
import io
from pathlib import Path
from typing import List, Optional
from datetime import datetime
from telegram import Bot, InputMediaPhoto, InputMediaVideo
from telegram.error import TelegramError, TimedOut, RetryAfter
from insta_archiver import config

logger = logging.getLogger(__name__)

# Telegram limits
MAX_CAPTION_LENGTH = 1024  # Telegram's caption limit
MAX_MEDIA_GROUP_SIZE = 10  # Telegram's media group limit


class FileTooLargeError(Exception):
    """Raised when a file exceeds the configured upload size limit"""

    pass


class TelegramClient:
    def __init__(self):
        # Initialize Bot with optional custom API URL for self-hosted server
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
        """
        Check if a file exceeds the configured size limit.

        Args:
            file_path: Path to the file to check

        Raises:
            FileTooLargeError: If file exceeds TELEGRAM_MAX_FILE_SIZE_MB
        """
        file_size_bytes = file_path.stat().st_size
        file_size_mb = file_size_bytes / (1024 * 1024)
        max_size_mb = config.TELEGRAM_MAX_FILE_SIZE_MB

        if file_size_mb > max_size_mb:
            raise FileTooLargeError(
                f"File {file_path.name} is {file_size_mb:.2f} MB (exceeds {max_size_mb} MB limit)"
            )

    def _check_media_group_size(self, file_paths: List[Path]) -> None:
        """
        Check if combined size of media group exceeds the configured limit.

        Args:
            file_paths: List of file paths in the media group

        Raises:
            FileTooLargeError: If total size exceeds TELEGRAM_MAX_FILE_SIZE_MB
        """
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
        media_type: int,
        max_retries: int = 3,
    ) -> List[int]:
        """Send media to Telegram with retry logic for timeouts"""
        # Check file sizes before attempting upload
        try:
            if len(file_paths) == 1:
                self._check_file_size(file_paths[0])
            else:
                # For media groups, check individual files and total size
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
                        chat_id, file_paths[0], caption, media_type
                    )
                elif len(file_paths) <= MAX_MEDIA_GROUP_SIZE:
                    return await self._send_media_group(chat_id, file_paths, caption)
                else:
                    # Split large albums into multiple groups
                    return await self._send_large_album(chat_id, file_paths, caption)
            except TimedOut as e:
                if attempt == max_retries:
                    logger.error(
                        f"Failed to send media to {chat_id} after {max_retries} attempts: {e}"
                    )
                    raise
                wait_time = 5 * attempt
                logger.warning(
                    f"Telegram timeout (attempt {attempt}/{max_retries}), retrying in {wait_time}s..."
                )
                await asyncio.sleep(wait_time)
            except RetryAfter as e:
                # Telegram flood control - wait the required time
                wait_time = e.retry_after + 1  # Add 1 second buffer
                logger.warning(
                    f"Flood control exceeded. Waiting {wait_time}s before retry (attempt {attempt}/{max_retries})..."
                )
                await asyncio.sleep(wait_time)
                # Don't count flood control as a failed attempt
                if attempt < max_retries:
                    continue
                else:
                    logger.error(
                        f"Failed to send media to {chat_id} after {max_retries} attempts with flood control"
                    )
                    raise
            except TelegramError as e:
                logger.error(f"Failed to send media to {chat_id}: {e}")
                raise

        raise RuntimeError(f"Failed to send media after {max_retries} attempts")

    async def _send_single_media(
        self, chat_id: int, file_path: Path, caption: str, media_type: int
    ) -> List[int]:
        """Send single media item, handling long captions"""
        # Check if caption is too long
        if len(caption) > MAX_CAPTION_LENGTH:
            # Send media first with truncated caption
            truncated_caption = (
                caption[: MAX_CAPTION_LENGTH - 50]
                + "\n\n... (caption too long, see next message)"
            )

            with open(file_path, "rb") as f:
                if media_type == 1:
                    msg = await self.bot.send_photo(
                        chat_id=chat_id,
                        photo=f,
                        caption=truncated_caption,
                        read_timeout=60,
                        write_timeout=60,
                    )
                else:
                    msg = await self.bot.send_video(
                        chat_id=chat_id,
                        video=f,
                        caption=truncated_caption,
                        read_timeout=60,
                        write_timeout=60,
                    )

            # Send full caption as text file
            caption_file = io.BytesIO(caption.encode("utf-8"))
            caption_file.name = "caption.txt"
            await self.bot.send_document(
                chat_id=chat_id,
                document=caption_file,
                filename="caption.txt",
                caption="📝 Full caption",
            )

            return [msg.message_id]
        else:
            # Normal send with full caption
            with open(file_path, "rb") as f:
                if media_type == 1:
                    msg = await self.bot.send_photo(
                        chat_id=chat_id,
                        photo=f,
                        caption=caption,
                        read_timeout=60,
                        write_timeout=60,
                    )
                else:
                    msg = await self.bot.send_video(
                        chat_id=chat_id,
                        video=f,
                        caption=caption,
                        read_timeout=60,
                        write_timeout=60,
                    )
            return [msg.message_id]

    async def _send_media_group(
        self, chat_id: int, file_paths: List[Path], caption: str
    ) -> List[int]:
        """Send media group (album), handling long captions"""
        # Check if caption is too long
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

        # Send media group with increased timeout
        messages = await self.bot.send_media_group(
            chat_id=chat_id, media=media_group, read_timeout=120, write_timeout=120
        )

        # If caption was truncated, send full caption as text file
        if len(caption) > MAX_CAPTION_LENGTH:
            caption_file = io.BytesIO(caption.encode("utf-8"))
            caption_file.name = "caption.txt"
            await self.bot.send_document(
                chat_id=chat_id,
                document=caption_file,
                filename="caption.txt",
                caption="📝 Full caption",
            )

        return [msg.message_id for msg in messages]

    async def _send_large_album(
        self, chat_id: int, file_paths: List[Path], caption: str
    ) -> List[int]:
        """Send large albums (>10 items) by splitting into multiple groups"""
        logger.info(
            f"Album has {len(file_paths)} items, splitting into groups of {MAX_MEDIA_GROUP_SIZE}"
        )

        all_message_ids = []

        # Split into chunks of MAX_MEDIA_GROUP_SIZE
        for i in range(0, len(file_paths), MAX_MEDIA_GROUP_SIZE):
            chunk = file_paths[i : i + MAX_MEDIA_GROUP_SIZE]
            chunk_num = (i // MAX_MEDIA_GROUP_SIZE) + 1
            total_chunks = (
                len(file_paths) + MAX_MEDIA_GROUP_SIZE - 1
            ) // MAX_MEDIA_GROUP_SIZE

            # Add chunk info to caption for first chunk only
            if i == 0:
                chunk_caption = f"{caption}\n\n📦 Part {chunk_num}/{total_chunks}"
            else:
                chunk_caption = f"📦 Part {chunk_num}/{total_chunks} (continued)"

            # Send this chunk
            message_ids = await self._send_media_group(chat_id, chunk, chunk_caption)
            all_message_ids.extend(message_ids)

            # Small delay between chunks to avoid rate limits
            if i + MAX_MEDIA_GROUP_SIZE < len(file_paths):
                await asyncio.sleep(1)

        return all_message_ids

    async def send_error_notification(
        self, error_type: str, context: str, traceback: str
    ):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""🚨 Error in {context}

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
        original_caption: Optional[str],
        author_username: str,
        post_code: str,
        taken_at: datetime,
        collection_name: Optional[str] = None,
        shared_by_username: Optional[str] = None,
    ) -> str:
        caption_parts = []

        if original_caption:
            caption_parts.append(original_caption)

        # Add collection tag for saved posts
        if collection_name:
            caption_parts.append(f"\n📁 {collection_name}")

        caption_parts.append(f"👤 @{author_username}")

        # Add shared by info for DM posts
        if shared_by_username:
            caption_parts.append(f"📤 Shared by @{shared_by_username}")

        caption_parts.append(f"🔗 https://instagram.com/p/{post_code}")
        caption_parts.append(f"📅 {taken_at.strftime('%Y-%m-%d %H:%M:%S')}")

        return "\n".join(caption_parts)
