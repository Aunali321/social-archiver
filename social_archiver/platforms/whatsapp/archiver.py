"""Ingesting WhatsApp from the phone-backup stores on disk.

Every configured store is scanned in full every run: the files are local, so a pass costs
little, and dedupe by id resumes an interrupted ingest exactly — the same reasoning as the
Reddit dump reader. `fetch_all` and `retry_failed` change nothing here; both exist for the
shared job signature.

Media is linked in at insert, not downloaded: the store's file is hardlinked into the
archive's downloads folder and the row inserts already archived. A message whose file the
store no longer holds keeps its `media_count` as the record of the loss — WhatsApp media
lives encrypted on a CDN that expires it, so no later job could fetch it anyway.
"""

import logging
import os
import shutil
import traceback
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

from social_archiver.core.config import ConfigError
from social_archiver.core.database import Database, Item
from social_archiver.core.telegram_client import TelegramClient
from social_archiver.platforms.whatsapp import config
from social_archiver.platforms.whatsapp.fetchers.android import AndroidStore
from social_archiver.platforms.whatsapp.fetchers.bridge import BridgeStore
from social_archiver.platforms.whatsapp.fetchers.ios import IOSStore
from social_archiver.platforms.whatsapp.port import WhatsAppPort
from social_archiver.platforms.whatsapp.simple_message import WhatsAppMessage

logger = logging.getLogger(__name__)

CATEGORIES = ("dm", "group")


class Store(Protocol):
    """A local holding of WhatsApp messages: a phone backup now, the bridge mirror later."""

    name: str

    def walk(self) -> AsyncIterator[list[WhatsAppMessage]]: ...


class ArchiveJob:
    def __init__(self, db: Database, port: WhatsAppPort, tg: TelegramClient | None = None):
        self.db = db
        self.port = port
        self.tg = tg

    async def run(self, fetch_all: bool = False, category: str | None = None, retry_failed: bool = False):
        for store in _stores():
            try:
                await self._ingest(store, category)
            except Exception as e:
                logger.error(f"Ingesting the {store.name} failed: {e}", exc_info=True)
                if self.tg:
                    await self.tg.send_error_notification(
                        type(e).__name__, f"archive:{store.name}", traceback.format_exc()
                    )

    async def _ingest(self, store: Store, category: str | None):
        recorded = 0
        async for batch in store.walk():
            messages = [message for message in batch if category in (None, message.kind)]
            if not messages:
                continue
            held = await self.db.held([message.item_id for message in messages])
            items = []
            for message in messages:
                if message.item_id in held:
                    continue
                item = message.to_item(message.kind)
                item.local_paths = _link_media(message, item, self.port.downloads_folder(item))
                items.append(item)
            await self.db.insert_many(items)
            recorded += len(items)
            if items:
                logger.info(f"{store.name}: recorded {len(items)} of {len(batch)} ({recorded} so far)")
        logger.info(f"{store.name}: {recorded} new message(s)")


def _stores() -> list[Store]:
    stores: list[Store] = []
    if config.WHATSAPP_EXPORT_DIR:
        folder = Path(config.WHATSAPP_EXPORT_DIR)
        if not folder.exists():
            raise ConfigError(f"WHATSAPP_EXPORT_DIR does not exist: {folder}")
        if android := AndroidStore.find(folder, config.WHATSAPP_BACKUP_KEY):
            stores.append(android)
        if ios := IOSStore.find(folder):
            stores.append(ios)
        if not stores:
            raise ConfigError(f"Nothing to ingest in {folder}: no msgstore.db(.crypt15) and no iOS backup")
    if bridge := BridgeStore.find(config.BRIDGE_DIR):
        stores.append(bridge)
    if not stores:
        raise ConfigError("Nothing to ingest: no phone backup in WHATSAPP_EXPORT_DIR and the bridge is not paired")
    logger.info(f"Ingesting from: {', '.join(store.name for store in stores)}")
    return stores


def _link_media(message: WhatsAppMessage, item: Item, folder: Path) -> list[Path]:
    """Hardlink what the store holds into the downloads folder, named the way the shared
    downloader names files, falling back to a copy across filesystems. The store's copy
    stays canonical; these links are what upload, embed and cleanup are allowed to touch."""
    paths = []
    for index, media in enumerate(message.media):
        if media.path is None:
            continue
        suffix = media.suffix or media.path.suffix
        stem = item.item_id if len(message.media) == 1 else f"{item.item_id}_{index}"
        target = folder / f"{stem}{suffix}"
        if not target.exists():
            folder.mkdir(parents=True, exist_ok=True)
            try:
                os.link(media.path, target)
            except OSError:
                # Across filesystems the file is copied instead. Data only: replaying the
                # source's permissions onto an ACL-restricted dataset is refused, and the
                # archive has no use for them anyway.
                shutil.copyfile(media.path, target)
        paths.append(target)
    return paths
