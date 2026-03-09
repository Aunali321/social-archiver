#!/usr/bin/env python3
"""
Retry uploading media that failed due to file size limits.

This script is useful when you've set up a self-hosted Telegram Bot API server
with higher file size limits. It will retry all media that previously failed
with 'too large' errors.

Usage:
    python retry_large_files.py [--category CATEGORY] [--limit LIMIT] [--dry-run]

Examples:
    # Retry all failed large files
    python retry_large_files.py

    # Retry only from 'saved' category
    python retry_large_files.py --category saved

    # See what would be retried without actually uploading
    python retry_large_files.py --dry-run

    # Retry only first 10 items
    python retry_large_files.py --limit 10

Prerequisites:
    - Self-hosted Telegram Bot API server running
    - TELEGRAM_BOT_API_URL configured in .env (e.g., http://localhost:8081)
    - TELEGRAM_MAX_FILE_SIZE_MB set higher than default 50 MB
"""

import asyncio
import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from insta_archiver import config
from insta_archiver.database import Database
from insta_archiver.telegram_client import TelegramClient, FileTooLargeError
from insta_archiver.instagram_client import InstagramClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def retry_large_files(
    category: Optional[str] = None, limit: Optional[int] = None, dry_run: bool = False
):
    """
    Retry uploading media that failed due to file size limits.

    Args:
        category: Optional category filter ('saved', 'likes', 'shared')
        limit: Maximum number of items to retry
        dry_run: If True, only show what would be retried without uploading
    """
    # Validate configuration
    if not config.TELEGRAM_BOT_API_URL and not dry_run:
        logger.error(
            "⚠️  TELEGRAM_BOT_API_URL is not configured. "
            "You need a self-hosted Telegram Bot API server to upload large files."
        )
        logger.info(
            "See: https://github.com/tdlib/telegram-bot-api for setup instructions"
        )
        return

    logger.info(f"🔍 Searching for failed large files...")
    if category:
        logger.info(f"   Category filter: {category}")
    if limit:
        logger.info(f"   Limit: {limit}")
    if dry_run:
        logger.info("   DRY RUN MODE - No uploads will be performed")

    async with Database() as db:
        # Get failed large files
        failed_media = await db.get_failed_large_files(category=category)

        if not failed_media:
            logger.info("✅ No failed large files found!")
            return

        logger.info(f"📊 Found {len(failed_media)} failed large files")

        # Apply limit if specified
        if limit:
            failed_media = failed_media[:limit]
            logger.info(f"   Processing first {len(failed_media)} items")

        if dry_run:
            logger.info("\n📋 Items that would be retried:")
            for idx, media in enumerate(failed_media, 1):
                local_paths = eval(media["local_paths"]) if media["local_paths"] else []
                total_size_mb = 0
                if local_paths:
                    for path_str in local_paths:
                        path = Path(path_str)
                        if path.exists():
                            total_size_mb += path.stat().st_size / (1024 * 1024)

                logger.info(
                    f"   {idx}. Media {media['media_pk']} - "
                    f"{len(local_paths)} file(s), "
                    f"{total_size_mb:.2f} MB total"
                )
                logger.info(f"      Error: {media['error_message']}")
            return

        # Initialize clients
        logger.info("\n🚀 Starting retry process...")
        tg_client = TelegramClient()
        ig_client = InstagramClient()
        await ig_client.login()

        # Map category to chat ID
        chat_mapping = {
            "saved": config.TELEGRAM_CHAT_SAVED,
            "likes": config.TELEGRAM_CHAT_LIKES,
            "shared": config.TELEGRAM_CHAT_SHARED,
        }

        success_count = 0
        still_too_large_count = 0
        error_count = 0

        for idx, media in enumerate(failed_media, 1):
            media_pk = media["media_pk"]
            media_category = media["category"]
            chat_id = chat_mapping.get(media_category)

            if not chat_id:
                logger.warning(
                    f"⚠️  [{idx}/{len(failed_media)}] Unknown category '{media_category}' for media {media_pk}"
                )
                continue

            logger.info(
                f"🔄 [{idx}/{len(failed_media)}] Retrying media {media_pk} ({media_category})"
            )

            # Parse local paths
            local_paths = eval(media["local_paths"]) if media["local_paths"] else []
            if not local_paths:
                logger.warning(f"   No local files found, skipping")
                error_count += 1
                continue

            file_paths = [Path(p) for p in local_paths]

            # Check if files exist
            missing_files = [p for p in file_paths if not p.exists()]
            if missing_files:
                logger.warning(
                    f"   Missing {len(missing_files)} file(s), skipping: {missing_files[0]}"
                )
                error_count += 1
                continue

            # Log file sizes
            total_size_mb = sum(p.stat().st_size for p in file_paths) / (1024 * 1024)
            logger.info(
                f"   Files: {len(file_paths)}, Total size: {total_size_mb:.2f} MB"
            )

            try:
                # Format caption
                caption = tg_client.format_caption(
                    original_caption=media.get("caption"),
                    author_username=media["author_username"],
                    post_code=media["media_code"],
                    taken_at=media["taken_at"],
                )

                # Attempt upload
                message_ids = await tg_client.send_media(
                    chat_id=chat_id,
                    file_paths=file_paths,
                    caption=caption,
                    media_type=media["media_type"],
                )

                # Mark as uploaded
                await db.mark_uploaded(media_pk, message_ids)
                logger.info(f"   ✅ Successfully uploaded! Message IDs: {message_ids}")
                success_count += 1

            except FileTooLargeError as e:
                logger.warning(f"   ⚠️  Still too large: {e}")
                still_too_large_count += 1
                # Update error message to include current limit
                await db.update_status(media_pk, "failed", str(e))

            except Exception as e:
                logger.error(f"   ❌ Failed to upload: {e}")
                error_count += 1
                await db.update_status(media_pk, "failed", str(e))

            # Small delay between uploads
            await asyncio.sleep(1)

        # Summary
        logger.info("\n" + "=" * 60)
        logger.info("📊 Retry Summary:")
        logger.info(f"   ✅ Successfully uploaded: {success_count}")
        logger.info(f"   ⚠️  Still too large: {still_too_large_count}")
        logger.info(f"   ❌ Errors: {error_count}")
        logger.info("=" * 60)

        if still_too_large_count > 0:
            logger.info(
                f"\n💡 Tip: Increase TELEGRAM_MAX_FILE_SIZE_MB in .env "
                f"(currently: {config.TELEGRAM_MAX_FILE_SIZE_MB} MB)"
            )


def main():
    parser = argparse.ArgumentParser(
        description="Retry uploading media that failed due to file size limits",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--category",
        choices=["saved", "likes", "shared"],
        help="Only retry files from this category",
    )
    parser.add_argument("--limit", type=int, help="Maximum number of items to retry")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be retried without actually uploading",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            retry_large_files(
                category=args.category, limit=args.limit, dry_run=args.dry_run
            )
        )
    except KeyboardInterrupt:
        logger.info("\n⚠️  Interrupted by user")
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
