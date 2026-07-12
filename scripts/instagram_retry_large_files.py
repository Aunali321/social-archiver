#!/usr/bin/env python3
"""Retry uploading Instagram media that previously failed due to Telegram's file size limit.

Usage:
    uv run python scripts/instagram_retry_large_files.py [--category CATEGORY] [--limit LIMIT] [--dry-run]

Prerequisites:
    - Self-hosted Telegram Bot API server running
    - TELEGRAM_BOT_API_URL configured in .env (e.g., http://localhost:8081)
    - TELEGRAM_MAX_FILE_SIZE_MB set higher than the default 50 MB
"""
import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_archiver.core.database import Database
from social_archiver.core.telegram_client import FileTooLargeError, TelegramClient
from social_archiver.platforms.instagram import config
from social_archiver.platforms.instagram.client import InstagramClient
from social_archiver.platforms.instagram.processor import format_caption
from scripts.instagram_retry_failed_embeddings import _simple_media_from_row

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PLATFORM = "instagram"


async def retry_large_files(category: str | None = None, limit: int | None = None, dry_run: bool = False):
    if not config.TELEGRAM_BOT_API_URL and not dry_run:
        logger.error("TELEGRAM_BOT_API_URL is not configured — a self-hosted Bot API server is required for large uploads.")
        logger.info("See: https://github.com/tdlib/telegram-bot-api for setup instructions")
        return

    async with Database(config.DATABASE_PATH) as db:
        failed_media = await db.get_failed_large_files(PLATFORM, category=category)

        if not failed_media:
            logger.info("No failed large files found")
            return

        logger.info(f"Found {len(failed_media)} failed large files")
        if limit:
            failed_media = failed_media[:limit]

        if dry_run:
            for idx, media in enumerate(failed_media, 1):
                local_paths = json.loads(media["local_paths"]) if media["local_paths"] else []
                total_mb = sum(Path(p).stat().st_size for p in local_paths if Path(p).exists()) / (1024 * 1024)
                logger.info(f"   {idx}. {media['item_id']} - {len(local_paths)} file(s), {total_mb:.2f} MB total")
                logger.info(f"      Error: {media['error_message']}")
            return

        tg_client = TelegramClient()
        ig_client = InstagramClient()
        ig_client.login()

        success_count = still_too_large_count = error_count = 0

        for idx, media in enumerate(failed_media, 1):
            item_id = media["item_id"]
            chat_id = {
                "saved": config.TELEGRAM_CHAT_SAVED,
                "likes": config.TELEGRAM_CHAT_LIKES,
                "shared": config.TELEGRAM_CHAT_SHARED,
            }.get(media["category"])

            if not chat_id:
                logger.warning(f"[{idx}/{len(failed_media)}] Unknown category '{media['category']}' for {item_id}")
                continue

            logger.info(f"[{idx}/{len(failed_media)}] Retrying {item_id} ({media['category']})")

            local_paths = json.loads(media["local_paths"]) if media["local_paths"] else []
            file_paths = [Path(p) for p in local_paths]

            missing = [p for p in file_paths if not p.exists()]
            if not file_paths or missing:
                logger.warning(f"   Missing local file(s), skipping: {missing[0] if missing else '(none found)'}")
                error_count += 1
                continue

            total_mb = sum(p.stat().st_size for p in file_paths) / (1024 * 1024)
            logger.info(f"   Files: {len(file_paths)}, total size: {total_mb:.2f} MB")

            try:
                simple_media = _simple_media_from_row(media)
                caption = format_caption(
                    simple_media,
                    collection_name=json.loads(media["metadata"]).get("collection_name") if media.get("metadata") else None,
                )
                message_ids = await tg_client.send_media(chat_id, file_paths, caption)
                await db.mark_uploaded(item_id, message_ids)
                logger.info(f"   Uploaded successfully, message IDs: {message_ids}")
                success_count += 1

            except FileTooLargeError as e:
                logger.warning(f"   Still too large: {e}")
                still_too_large_count += 1
                await db.update_status(item_id, "failed", str(e))

            except Exception as e:
                logger.error(f"   Failed to upload: {e}")
                error_count += 1
                await db.update_status(item_id, "failed", str(e))

            await asyncio.sleep(1)

        logger.info("=" * 60)
        logger.info(f"Successfully uploaded: {success_count}")
        logger.info(f"Still too large: {still_too_large_count}")
        logger.info(f"Errors: {error_count}")
        logger.info("=" * 60)

        if still_too_large_count:
            logger.info(f"Tip: increase TELEGRAM_MAX_FILE_SIZE_MB in .env (currently {config.TELEGRAM_MAX_FILE_SIZE_MB} MB)")


def main():
    parser = argparse.ArgumentParser(description="Retry uploading Instagram media that failed due to file size limits")
    parser.add_argument("--category", choices=["saved", "likes", "shared"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        asyncio.run(retry_large_files(category=args.category, limit=args.limit, dry_run=args.dry_run))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
