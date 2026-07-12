#!/usr/bin/env python3
"""
Test: Fetch real tweets, expand, download media, caption with interleaved thread context.

Usage:
    GOOGLE_APPLICATION_CREDENTIALS=service-account.json \
      .venv/bin/python test_thread_captions.py [--likes N]
"""
import asyncio
import argparse
import logging
import json
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("test")

from social_archiver.platforms.twitter.client import TwitterClient
from social_archiver.platforms.twitter.expander import TweetExpander
from social_archiver.platforms.twitter.downloader import MediaDownloader
from social_archiver.llm.vertex_client import VertexVLMClient


def get_mime(path: Path) -> str | None:
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp",
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    }.get(path.suffix.lower())


def build_parts(tweets, media_paths):
    parts = []
    for t in tweets:
        text = (t.text or "").strip()
        author = f"@{t.author_username}"
        origin = f"[{t.origin}] " if t.origin and t.origin != "liked" else ""
        label = f"[tweet_id:{t.id}] {origin}{author}: {text}" if text else f"[tweet_id:{t.id}] {origin}{author}"

        if t.quoted_tweet_id:
            q = next((x for x in tweets if x.id == t.quoted_tweet_id), None)
            if q:
                qt = (q.text or "").strip()
                if qt:
                    label += f"\n  ↳ Quotes @{q.author_username}: {qt}"

        parts.append({"type": "text", "text": label, "tweet_id": t.id})

        for idx, p in enumerate(media_paths.get(t.id, [])):
            mime = get_mime(p)
            if mime:
                parts.append({
                    "type": "media", "path": p, "mime_type": mime,
                    "tweet_id": t.id, "media_index": idx,
                })
    return parts


async def main(n_likes: int):
    tw = TwitterClient()
    await tw.verify_credentials()

    log.info(f"Fetching {n_likes} likes...")
    result = await tw.get_all_likes(limit=n_likes, page_delay=1.5)
    raw = result.get("tweets", [])
    log.info(f"Got {len(raw)} likes")

    log.info("Expanding...")
    expander = TweetExpander(tw, page_delay=1.5)
    expanded = await expander.expand(raw)
    expanded = [t for t in expanded if not t.is_tombstone]
    expanded.sort(key=lambda t: t.created_at or datetime(1970, 1, 1, tzinfo=timezone.utc))
    log.info(f"Expanded: {len(expanded)} tweets")

    dl = MediaDownloader()
    media_paths = {}
    for t in expanded:
        if t.has_media:
            paths = await dl.download_tweet_media(t)
            if paths:
                media_paths[t.id] = paths

    media_count = sum(len(v) for v in media_paths.values())
    log.info(f"Downloaded {media_count} media files for {len(media_paths)} tweets")

    if not media_paths:
        log.info("No media in expanded set")
        return

    parts = build_parts(expanded, media_paths)

    print("\n" + "=" * 60)
    print("INTERLEAVED PARTS")
    print("=" * 60)
    for i, p in enumerate(parts):
        if p["type"] == "text":
            print(f"\n  [{i}] TEXT [{p['tweet_id']}]:")
            for line in p["text"].split("\n"):
                print(f"       {line}")
        else:
            print(f"  [{i}] MEDIA [{p['tweet_id']}#{p['media_index']}]: {p['path'].name} ({p['mime_type']})")

    n_media = sum(1 for p in parts if p["type"] == "media")
    print(f"\nTotal: {len(expanded)} tweets, {n_media} media, {len(parts)} parts")
    print("=" * 60)

    vlm = VertexVLMClient(model="gemini-3-flash-preview")
    log.info(f"Calling VLM with {len(parts)} parts ({n_media} media)...")

    result = await vlm.describe_thread(parts)
    if not result:
        print("\nVLM CALL FAILED")
        return

    print("\n" + "=" * 60)
    print(f"{len(result.captions)} CAPTIONS")
    print("=" * 60)

    for c in result.captions:
        print(f"\n--- [{c.tweet_id}#{c.media_index}] ---")
        print(f"VISUAL: {c.visual_description}")
        if c.visible_text:
            print(f"\nTEXT: {c.visible_text}")
        if c.speech_transcript:
            print(f"\nSPEECH: {c.speech_transcript}")
        if c.audio_description:
            print(f"\nAUDIO: {c.audio_description}")

    # Also dump as JSON for inspection
    out_path = Path("test_thread_captions_output.json")
    out_data = [
        {
            "tweet_id": c.tweet_id,
            "media_index": c.media_index,
            "visual_description": c.visual_description,
            "visible_text": c.visible_text,
            "speech_transcript": c.speech_transcript,
            "audio_description": c.audio_description,
        }
        for c in result.captions
    ]
    out_path.write_text(json.dumps(out_data, indent=2, ensure_ascii=False))
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--likes", type=int, default=10, help="Number of likes to fetch")
    args = parser.parse_args()
    asyncio.run(main(args.likes))
