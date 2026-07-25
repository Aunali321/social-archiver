#!/usr/bin/env python3
"""
Delete Telegram messages from saved channel that were sent on or before January 31st.
Uses Telethon to read channel history and delete old messages.

Usage:
    uv run python delete_old_posts.py --dry-run   # See what would be deleted
    uv run python delete_old_posts.py             # Actually delete
"""

import asyncio
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv
import os

load_dotenv()

# You need to get these from https://my.telegram.org
API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")
CHAT_ID = int(os.getenv("TELEGRAM_CHAT_SAVED", "0"))

# Cutoff date - delete messages sent to Telegram on or before this date
CUTOFF_DATE = datetime(2026, 1, 30, 23, 59, 59, tzinfo=timezone.utc)

# Check for dry run flag
DRY_RUN = "--dry-run" in sys.argv or "-n" in sys.argv


async def main():
    from telethon import TelegramClient

    if not API_ID or not API_HASH:
        print("ERROR: Please set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")
        print("Get them from https://my.telegram.org")
        return

    if not CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_SAVED not set in .env")
        return

    client = TelegramClient("delete_session", int(API_ID), API_HASH)
    await client.start()

    print("Connected to Telegram")
    print(f"Target channel ID: {CHAT_ID}")
    print(f"Cutoff date: {CUTOFF_DATE.strftime('%Y-%m-%d')}")
    print(f"Will delete messages sent on or before {CUTOFF_DATE.strftime('%Y-%m-%d')}")
    if DRY_RUN:
        print("*** DRY RUN MODE - No messages will be deleted ***")
    print("-" * 50)

    # Get the channel entity
    try:
        entity = await client.get_entity(CHAT_ID)
        print(f"Found channel: {getattr(entity, 'title', CHAT_ID)}")
    except Exception as e:
        print(f"ERROR: Could not find channel {CHAT_ID}: {e}")
        await client.disconnect()
        return

    messages_to_delete = []
    total_scanned = 0

    print("Scanning messages...")

    oldest_date = None
    newest_date = None
    first_kept_message = None
    last_deleted_message = None

    async for message in client.iter_messages(entity):
        total_scanned += 1

        if message.date:
            if oldest_date is None or message.date < oldest_date:
                oldest_date = message.date
            if newest_date is None or message.date > newest_date:
                newest_date = message.date

        if total_scanned % 100 == 0:
            print(f"  Scanned {total_scanned} messages, found {len(messages_to_delete)} to delete...")

        # Check message date (when it was sent to Telegram)
        if message.date and message.date <= CUTOFF_DATE:
            messages_to_delete.append(message.id)
            if last_deleted_message is None:
                last_deleted_message = message
        else:
            # Track the oldest message we're keeping (first one NOT deleted)
            if first_kept_message is None or (message.date and message.date < first_kept_message.date):
                first_kept_message = message

    print(f"\nOldest message date: {oldest_date}")
    print(f"Newest message date: {newest_date}")
    print(f"Cutoff date: {CUTOFF_DATE}")

    if first_kept_message:
        print("\nFirst message KEPT (not deleted):")
        print(f"  Date (UTC): {first_kept_message.date}")
        print(f"  Date (IST): {first_kept_message.date.strftime('%Y-%m-%d %H:%M:%S')} UTC + 5:30")
        caption_preview = (first_kept_message.text or first_kept_message.message or "")[:100]
        print(f"  Caption preview: {caption_preview}...")

    if last_deleted_message:
        print("\nLast message TO BE DELETED:")
        print(f"  Date (UTC): {last_deleted_message.date}")
        caption_preview = (last_deleted_message.text or last_deleted_message.message or "")[:100]
        print(f"  Caption preview: {caption_preview}...")

    print("-" * 50)
    print(f"Total messages scanned: {total_scanned}")
    print(f"Messages to delete: {len(messages_to_delete)}")

    if not messages_to_delete:
        print("No messages to delete.")
        await client.disconnect()
        return

    if DRY_RUN:
        print("\n*** DRY RUN - No messages were deleted ***")
        await client.disconnect()
        return

    # Confirm before deleting
    confirm = input(f"\nAre you sure you want to delete {len(messages_to_delete)} messages? (yes/no): ")
    if confirm.lower() != "yes":
        print("Aborted.")
        await client.disconnect()
        return

    print("\nDeleting messages...")

    # Delete in batches of 100 (Telegram limit)
    deleted_count = 0
    for i in range(0, len(messages_to_delete), 100):
        batch = messages_to_delete[i : i + 100]
        try:
            await client.delete_messages(entity, batch)
            deleted_count += len(batch)
            print(f"  Deleted {deleted_count}/{len(messages_to_delete)} messages...")
        except Exception as e:
            print(f"  ERROR deleting batch: {e}")

    print(f"\nDone! Deleted {deleted_count} messages.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
