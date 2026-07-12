#!/usr/bin/env python3
"""
Retry generating embeddings for media that failed or was skipped.

This script processes media that:
- Has been uploaded to Telegram but has no embedding
- Previously failed embedding generation (e.g., due to payment issues)

Usage:
    python retry_failed_embeddings.py [--category CATEGORY] [--limit LIMIT] [--dry-run]

Examples:
    # Retry all failed/missing embeddings
    python retry_failed_embeddings.py

    # Retry only from 'saved' category
    python retry_failed_embeddings.py --category saved

    # See what would be retried without actually processing
    python retry_failed_embeddings.py --dry-run

    # Retry only first 10 items
    python retry_failed_embeddings.py --limit 10

Prerequisites:
    - EMBEDDING_ENABLED=true in .env
    - OPENROUTER_API_KEY or GEMINI_API_KEY configured based on VLM_PROVIDER
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from insta_archiver import config
from insta_archiver.database import Database
from insta_archiver.downloader import MediaDownloader
from insta_archiver.embedding_processor import EmbeddingProcessor
from insta_archiver.instagram_client import InstagramClient
from insta_archiver.milvus_manager import MilvusManager
from insta_archiver.simple_media import SimpleMedia, SimpleUser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def create_simple_media_from_db(row: dict) -> SimpleMedia:
    """Create a SimpleMedia object from database row"""
    from datetime import datetime

    # Parse metadata if exists
    metadata = {}
    if row.get("metadata"):
        try:
            metadata = json.loads(row["metadata"])
        except:
            pass

    return SimpleMedia(
        pk=str(row["media_pk"]),
        id=row["media_id"],
        code=row["media_code"],
        media_type=row["media_type"],
        caption_text=row.get("caption") or "",
        user=SimpleUser(
            pk=str(row["author_user_id"]),
            username=row["author_username"],
        ),
        taken_at=row["taken_at"]
        if isinstance(row["taken_at"], datetime)
        else datetime.fromisoformat(row["taken_at"]),
        product_type=row.get("product_type") or "feed",
        collection_name=metadata.get("collection_name"),
        shared_by_username=metadata.get("shared_by_username"),
    )


async def process_single_media(
    media_row: dict,
    embedding_processor: EmbeddingProcessor,
    downloader: MediaDownloader,
    db: Database,
    idx: int,
    total: int,
):
    """Process a single media item for embedding."""
    media_pk = media_row["media_pk"]
    logger.info(
        f"🔄 [{idx}/{total}] Processing media {media_pk} "
        f"(@{media_row['author_username']})"
    )

    success_count = 0
    download_failed_count = 0
    embedding_failed_count = 0

    local_paths_json = None
    local_paths = []

    try:
        local_paths_json = media_row.get("local_paths")

        if local_paths_json:
            try:
                local_paths = [Path(p) for p in json.loads(local_paths_json)]
                local_paths = [p for p in local_paths if p.exists()]
            except:
                local_paths = []

        if not local_paths:
            logger.info("   📥 Downloading media...")

            simple_media = create_simple_media_from_db(media_row)

            local_paths = await downloader.download_media(
                simple_media, media_row["category"]
            )

            if not local_paths:
                logger.warning("   ⚠️ Failed to download, skipping")
                return 0, 1, 0

        logger.info(f"   📁 Using {len(local_paths)} file(s)")

        simple_media = create_simple_media_from_db(media_row)

        result = await embedding_processor.process_media(
            media_pk=media_pk,
            media=simple_media,
            category=media_row["category"],
            local_paths=local_paths,
        )

        if isinstance(result, tuple):
            success, vlm_description = result
        else:
            success = result
            vlm_description = None

        if success:
            await db.mark_embedded(media_pk, True, vlm_description=vlm_description)
            logger.info("   ✅ Embedding generated successfully!")
            return 1, 0, 0
        else:
            await db.mark_embedded(media_pk, False, "Embedding generation failed")
            logger.warning("   ❌ Embedding generation failed")
            return 0, 0, 1

    except Exception as e:
        logger.error(f"   ❌ Error: {e}")
        await db.mark_embedded(media_pk, False, str(e))
        return 0, 0, 1

    finally:
        if config.CLEANUP_DOWNLOADS and local_paths_json is None and local_paths:
            for path in local_paths:
                try:
                    if path.exists():
                        path.unlink()
                except:
                    pass


async def retry_failed_embeddings(
    category: Optional[str] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    failed_only: bool = False,
    batch_size: int = 15,
):
    """
    Retry generating embeddings for failed/missing media.

    Args:
        category: Optional category filter ('saved', 'likes', 'shared')
        limit: Maximum number of items to process
        dry_run: If True, only show what would be processed
        failed_only: If True, only retry items with 'failed' status (not NULL)
        batch_size: Number of items to process concurrently
    """
    # Validate configuration
    if not config.EMBEDDING_ENABLED:
        logger.error("❌ EMBEDDING_ENABLED is not set to true in .env")
        return

    vlm_provider = config.VLM_PROVIDER.lower()

    if vlm_provider == "vertex":
        pass  # uses ADC (GOOGLE_APPLICATION_CREDENTIALS), no API key needed
    elif vlm_provider == "gemini":
        if not config.GEMINI_API_KEY:
            logger.error("❌ GEMINI_API_KEY is not configured in .env")
            return
    else:  # openrouter
        if not config.OPENROUTER_API_KEY:
            logger.error("❌ OPENROUTER_API_KEY is not configured in .env")
            return

    logger.info("🔍 Searching for media without embeddings...")
    if category:
        logger.info(f"   Category filter: {category}")
    if limit:
        logger.info(f"   Limit: {limit}")
    if dry_run:
        logger.info("   DRY RUN MODE - No embeddings will be generated")
    if failed_only:
        logger.info("   Processing only 'failed' items (not missing)")

    async with Database() as db:
        # Get media without embeddings
        media_list = await db.get_media_without_embeddings(category=category)

        if failed_only:
            media_list = [
                m for m in media_list if m.get("embedding_status") == "failed"
            ]

        if not media_list:
            logger.info("✅ No media found that needs embedding!")
            return

        logger.info(f"📊 Found {len(media_list)} media items to process")

        # Apply limit if specified
        if limit:
            media_list = media_list[:limit]
            logger.info(f"   Processing first {len(media_list)} items")

        if dry_run:
            logger.info("\n📋 Items that would be processed:")
            for idx, media in enumerate(media_list, 1):
                status = media.get("embedding_status") or "missing"
                logger.info(
                    f"   {idx}. Media {media['media_pk']} - "
                    f"@{media['author_username']} - "
                    f"type={media['media_type']} - "
                    f"embedding_status={status}"
                )
                if media.get("error_message"):
                    logger.info(f"      Error: {media['error_message'][:100]}")
            return

        # Initialize embedding system (VLM + local embeddings)
        logger.info("\n🚀 Initializing embedding system...")

        # Initialize the appropriate VLM client based on provider
        if vlm_provider == "vertex":
            from insta_archiver.vertex_client import VertexVLMClient

            vlm_client = VertexVLMClient(
                model=config.VERTEX_MODEL,
                project=config.VERTEX_PROJECT,
                location=config.VERTEX_LOCATION,
                timeout=config.EMBEDDING_TIMEOUT,
            )
            vlm_model_name = config.VERTEX_MODEL
        elif vlm_provider == "gemini":
            from insta_archiver.gemini_client import GeminiClient

            vlm_client = GeminiClient(
                api_key=config.GEMINI_API_KEY,
                model=config.GEMINI_MODEL,
                timeout=config.EMBEDDING_TIMEOUT,
            )
            vlm_model_name = config.GEMINI_MODEL
        else:  # openrouter
            from insta_archiver.vlm_client import VLMClient

            vlm_client = VLMClient(
                api_key=config.OPENROUTER_API_KEY,
                vlm_model=config.VLM_MODEL,
                timeout=config.EMBEDDING_TIMEOUT,
            )
            vlm_model_name = config.VLM_MODEL

        logger.info(f"   Provider: {vlm_provider}")
        logger.info(f"   Model: {vlm_model_name}")

        milvus_manager = MilvusManager(uri=config.INSTAGRAM_MILVUS_URI)
        milvus_manager.initialize_collections()

        embedding_processor = EmbeddingProcessor(vlm_client, milvus_manager)

        # Initialize Instagram client for downloading
        ig_client = InstagramClient()
        ig_client.login()
        downloader = MediaDownloader(ig_client)

        success_count = 0
        download_failed_count = 0
        embedding_failed_count = 0

        logger.info(f"\n🚀 Processing in batches of {batch_size}...")

        total_batches = (len(media_list) + batch_size - 1) // batch_size

        for batch_idx in range(0, len(media_list), batch_size):
            batch_num = batch_idx // batch_size + 1
            batch = media_list[batch_idx : batch_idx + batch_size]

            logger.info(f"\n📦 Batch {batch_num}/{total_batches} ({len(batch)} items)")

            tasks = [
                process_single_media(
                    media_row,
                    embedding_processor,
                    downloader,
                    db,
                    batch_idx + i + 1,
                    len(media_list),
                )
                for i, media_row in enumerate(batch)
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            batch_success = 0
            batch_download_failed = 0
            batch_embedding_failed = 0

            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"   ❌ Task error: {result}")
                    batch_embedding_failed += 1
                else:
                    s, d, e = result
                    batch_success += s
                    batch_download_failed += d
                    batch_embedding_failed += e

            success_count += batch_success
            download_failed_count += batch_download_failed
            embedding_failed_count += batch_embedding_failed

            logger.info(
                f"   Batch {batch_num} complete: "
                f"+{batch_success} success, +{batch_download_failed} dl failed, +{batch_embedding_failed} emb failed"
            )
            await asyncio.sleep(1)

        # Close Milvus
        milvus_manager.close()

        # Summary
        logger.info("\n" + "=" * 60)
        logger.info("📊 Backfill Summary:")
        logger.info(f"   ✅ Successfully embedded: {success_count}")
        logger.info(f"   📥 Download failed: {download_failed_count}")
        logger.info(f"   ❌ Embedding failed: {embedding_failed_count}")
        logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Retry generating embeddings for failed/missing media",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--category",
        choices=["saved", "likes", "shared"],
        help="Only process items from this category",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of items to process",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without actually generating embeddings",
    )
    parser.add_argument(
        "--failed-only",
        action="store_true",
        help="Only retry items with 'failed' status (not missing embeddings)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=15,
        help="Number of items to process concurrently (default: 15)",
    )

    args = parser.parse_args()

    try:
        asyncio.run(
            retry_failed_embeddings(
                category=args.category,
                limit=args.limit,
                dry_run=args.dry_run,
                failed_only=args.failed_only,
                batch_size=args.batch_size,
            )
        )
    except KeyboardInterrupt:
        logger.info("\n⚠️ Interrupted by user")
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
