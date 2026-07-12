#!/usr/bin/env python3
"""Retry generating embeddings for Instagram media that failed or was never embedded.

Usage:
    uv run python scripts/instagram_retry_failed_embeddings.py [--category CATEGORY] [--limit LIMIT] [--dry-run]

Prerequisites:
    - EMBEDDING_ENABLED=true in .env
    - VLM_PROVIDER credentials configured (Vertex ADC, GEMINI_API_KEY, or OPENROUTER_API_KEY)
"""
import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_archiver.core.database import Database
from social_archiver.core.milvus_manager import MilvusManager
from social_archiver.llm.factory import create_vlm_client
from social_archiver.platforms.instagram import config
from social_archiver.platforms.instagram.client import InstagramClient
from social_archiver.platforms.instagram.downloader import MediaDownloader
from social_archiver.platforms.instagram.embedding_processor import EmbeddingProcessor
from social_archiver.platforms.instagram.simple_media import SimpleMedia, SimpleUser

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PLATFORM = "instagram"


def _simple_media_from_row(row: dict) -> SimpleMedia:
    """Reconstruct a SimpleMedia good enough for re-embedding already-downloaded files.
    Note: Instagram CDN URLs aren't persisted, so this can't re-download expired media —
    only re-process files that are still present on disk (photo_url/video_url stay unset)."""
    metadata = json.loads(row["metadata"]) if row.get("metadata") else {}
    media_type = 8 if (row.get("media_count") or 0) > 1 else 1
    return SimpleMedia(
        pk=row["item_id"],
        id=row["item_id"],
        code=row["item_id"],
        media_type=media_type,
        caption_text=row.get("text") or "",
        user=SimpleUser(pk=row.get("author_id") or "", username=row["author_username"]),
        taken_at=row["created_at"] if isinstance(row["created_at"], datetime) else datetime.fromisoformat(row["created_at"]),
        collection_name=metadata.get("collection_name"),
        shared_by_username=metadata.get("shared_by_username"),
    )


async def _process_one(row: dict, embedding_processor: EmbeddingProcessor, downloader: MediaDownloader, db: Database, idx: int, total: int) -> tuple[int, int, int]:
    item_id = row["item_id"]
    logger.info(f"[{idx}/{total}] Processing {item_id} (@{row['author_username']})")

    local_paths = []
    if row.get("local_paths"):
        try:
            local_paths = [p for p in (Path(p) for p in json.loads(row["local_paths"])) if p.exists()]
        except Exception:
            local_paths = []

    downloaded_now = False
    try:
        if not local_paths:
            logger.info("   Downloading media...")
            media = _simple_media_from_row(row)
            local_paths = await downloader.download_media(media, row["category"])
            downloaded_now = True
            if not local_paths:
                logger.warning("   Failed to download, skipping")
                return 0, 1, 0

        media = _simple_media_from_row(row)
        success, vlm_description = await embedding_processor.process_media(item_id, media, row["category"], local_paths)

        if success:
            await db.mark_embedded(item_id, True, vlm_description=vlm_description)
            logger.info("   Embedding generated successfully")
            return 1, 0, 0

        await db.mark_embedded(item_id, False, "Embedding generation failed")
        logger.warning("   Embedding generation failed")
        return 0, 0, 1

    except Exception as e:
        logger.error(f"   Error: {e}")
        await db.mark_embedded(item_id, False, str(e))
        return 0, 0, 1

    finally:
        if config.CLEANUP_DOWNLOADS and downloaded_now:
            for path in local_paths:
                path.unlink(missing_ok=True)


async def retry_failed_embeddings(
    category: str | None = None, limit: int | None = None, dry_run: bool = False, failed_only: bool = False, batch_size: int = 15
):
    if not config.EMBEDDING_ENABLED:
        logger.error("EMBEDDING_ENABLED is not set to true in .env")
        return

    logger.info("Searching for media without embeddings...")

    async with Database(config.DATABASE_PATH) as db:
        media_list = await db.get_items_without_embeddings(PLATFORM, category=category)
        if failed_only:
            media_list = [m for m in media_list if m.get("embedding_status") == "failed"]

        if not media_list:
            logger.info("No media found that needs embedding")
            return

        logger.info(f"Found {len(media_list)} media items to process")
        if limit:
            media_list = media_list[:limit]

        if dry_run:
            for idx, media in enumerate(media_list, 1):
                status = media.get("embedding_status") or "missing"
                logger.info(f"   {idx}. {media['item_id']} - @{media['author_username']} - embedding_status={status}")
                if media.get("error_message"):
                    logger.info(f"      Error: {media['error_message'][:100]}")
            return

        vlm_client, vlm_model_name = create_vlm_client(config.VLM_PROVIDER)
        logger.info(f"Provider: {config.VLM_PROVIDER}, model: {vlm_model_name}")

        milvus_manager = MilvusManager(
            uri=config.INSTAGRAM_MILVUS_URI,
            collections={"likes": "instagram_likes", "saved": "instagram_saved", "shared": "instagram_shared"},
        )
        milvus_manager.initialize_collections()
        embedding_processor = EmbeddingProcessor(vlm_client, milvus_manager)

        ig_client = InstagramClient()
        ig_client.login()
        downloader = MediaDownloader()

        success_count = download_failed_count = embedding_failed_count = 0
        total_batches = (len(media_list) + batch_size - 1) // batch_size

        for batch_idx in range(0, len(media_list), batch_size):
            batch_num = batch_idx // batch_size + 1
            batch = media_list[batch_idx : batch_idx + batch_size]
            logger.info(f"Batch {batch_num}/{total_batches} ({len(batch)} items)")

            results = await asyncio.gather(
                *(
                    _process_one(row, embedding_processor, downloader, db, batch_idx + i + 1, len(media_list))
                    for i, row in enumerate(batch)
                ),
                return_exceptions=True,
            )

            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"   Task error: {result}")
                    embedding_failed_count += 1
                else:
                    s, d, e = result
                    success_count += s
                    download_failed_count += d
                    embedding_failed_count += e

            await asyncio.sleep(1)

        milvus_manager.close()

        logger.info("=" * 60)
        logger.info(f"Successfully embedded: {success_count}")
        logger.info(f"Download failed: {download_failed_count}")
        logger.info(f"Embedding failed: {embedding_failed_count}")
        logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Retry generating embeddings for failed/missing Instagram media")
    parser.add_argument("--category", choices=["saved", "likes", "shared"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--failed-only", action="store_true")
    parser.add_argument("--batch-size", type=int, default=15)
    args = parser.parse_args()

    try:
        asyncio.run(
            retry_failed_embeddings(
                category=args.category, limit=args.limit, dry_run=args.dry_run, failed_only=args.failed_only, batch_size=args.batch_size
            )
        )
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
