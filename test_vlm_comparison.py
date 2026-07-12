"""
Test script: Compare VLM models for media captioning via OpenRouter.
Fetches a few media tweets from likes, downloads media, runs all models.
"""
import asyncio
import base64
import hashlib
import httpx
import json
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

MODELS = [
    ("qwen/qwen3.5-35b-a3b", "strict", {"temperature": 0}),
    ("qwen/qwen3.5-35b-a3b", "strict", {"temperature": 0.2}),
    ("qwen/qwen3.5-35b-a3b", "strict", {"temperature": 0.6}),
    ("google/gemini-3-flash-preview", "strict", {"temperature": 0}),
]

IMAGE_PROMPT_ORIGINAL = """This image is attached to the following tweet:

---
Author: @{author}
Tweet: {tweet_text}
URL: {url}
---

Describe this image in detail in English. Include:
- Main subjects and their appearance
- Setting/environment/background
- Any visible text, signs, or captions (translate to English if in another language)
- Colors, mood, and visual style
- Notable objects or elements

Always respond in English, translating any non-English text you see.
Be comprehensive but concise."""

VIDEO_PROMPT_ORIGINAL = """This video is attached to the following tweet:

---
Author: @{author}
Tweet: {tweet_text}
URL: {url}
---

Describe this video in detail in English. Include:
- Visual content: scenes, subjects, actions, settings
- Audio content: FULLY transcribe ALL speech, narration, and dialogue word-for-word (translate to English if in another language)
- Any on-screen text (translate to English if needed)
- Music or sound effects if notable
- Overall mood and style

IMPORTANT: Transcribe ALL spoken words completely - do not summarize or skip any dialogue.
For speech/dialogue, use quotation marks and attribute to speakers if identifiable.
Always respond in English, translating any non-English content.
Be comprehensive but concise."""

IMAGE_PROMPT_STRICT = """You are an archival media captioner. Describe ONLY what is visually present.

Tweet context (for understanding, NOT for fabricating details):
@{author}: {tweet_text}

Rules:
- Describe what you SEE. Never infer or fabricate details not visible in the image.
- Translate all non-English text you can read in the image to English.
- Do not start with "This image shows" or "Based on the image". Just describe directly.
- No markdown headers. Write in flowing paragraphs.
- Be detailed and thorough.

Describe: subjects, setting, all visible text (translated), colors, objects, visual style."""

VIDEO_PROMPT_STRICT = """You are an archival media captioner. Describe ONLY what you can see and hear.

Tweet context (for understanding, NOT for fabricating details):
@{author}: {tweet_text}

CRITICAL RULES:
- For speech: ONLY transcribe words you can actually hear. If audio is unclear, say "inaudible" or "unclear". NEVER guess or fabricate dialogue.
- For on-screen text: ONLY transcribe text you can actually read in the video frames.
- Do not start with "This video shows". Just describe directly.
- No markdown headers. Write in flowing paragraphs.
- Be detailed and thorough.

Describe: visual scenes, subjects, actions, setting, all audible speech (transcribed verbatim), on-screen text (translated), music/sounds, mood."""


async def fetch_media_tweets(count=5, video_only=False, pages=5):
    """Fetch likes and return tweets that have media."""
    from social_archiver.platforms.twitter.client import TwitterClient

    client = TwitterClient()
    if not await client.verify_credentials():
        raise RuntimeError("Twitter auth failed")

    media_tweets = []
    cursor = None
    for page in range(pages):
        if page > 0:
            await asyncio.sleep(2)
        result = await client.get_likes(count=20, cursor=cursor)
        if not result.get("success"):
            break

        for tweet in result.get("tweets", []):
            media = tweet.get("media")
            if not media:
                continue
            if video_only:
                has_video = any(m.get("type") in ("video", "animated_gif") for m in media)
                if not has_video:
                    continue
            media_tweets.append(tweet)
            if len(media_tweets) >= count:
                break

        if len(media_tweets) >= count:
            break

        next_cursor = result.get("next_cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

    await client.close()
    return media_tweets


async def download_media(tweet, folder: Path) -> list[Path]:
    """Download media from a tweet, return local paths."""
    paths = []
    for idx, item in enumerate(tweet.get("media", [])):
        media_type = item.get("type")
        if media_type == "photo":
            url = f"{item['url']}:orig"
            ext = "jpg"
            if ".png" in item["url"]:
                ext = "png"
        elif media_type in ("video", "animated_gif"):
            url = item.get("video_url")
            if not url:
                continue
            ext = "mp4"
        else:
            continue

        filename = f"{tweet['id']}_{idx}.{ext}"
        path = folder / filename

        if not path.exists():
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http:
                resp = await http.get(url)
                resp.raise_for_status()
                path.write_bytes(resp.content)

        paths.append(path)
    return paths


async def call_vlm(model: str, media_path: Path, media_type: str, tweet: dict, prompt_style: str = "original", extra_params: dict = None) -> dict:
    """Call a single VLM model via OpenRouter. Returns dict with description, timing, tokens."""
    with open(media_path, "rb") as f:
        media_b64 = base64.b64encode(f.read()).decode("utf-8")

    # Build prompt with full tweet context
    tweet_text = tweet.get("text", "")
    author = tweet.get("author_username", "unknown")
    url = f"https://x.com/{author}/status/{tweet.get('id', '')}"

    # Select prompt template based on style
    if prompt_style == "strict":
        img_template = IMAGE_PROMPT_STRICT
        vid_template = VIDEO_PROMPT_STRICT
    else:
        img_template = IMAGE_PROMPT_ORIGINAL
        vid_template = VIDEO_PROMPT_ORIGINAL

    suffix = media_path.suffix.lower()
    if media_type == "image":
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "gif": "image/gif", "webp": "image/webp"}.get(suffix.lstrip("."), "image/jpeg")
        prompt = img_template.format(author=author, tweet_text=tweet_text, url=url)
        content = [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{media_b64}"}},
            {"type": "text", "text": prompt},
        ]
    else:
        mime = "video/mp4"
        prompt = vid_template.format(author=author, tweet_text=tweet_text, url=url)
        content = [
            {"type": "video_url", "video_url": {"url": f"data:{mime};base64,{media_b64}"}},
            {"type": "text", "text": prompt},
        ]

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 8192,
    }
    if extra_params:
        payload.update(extra_params)

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/insta-archiver",
    }

    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=180) as http:
            resp = await http.post(OPENROUTER_URL, headers=headers, json=payload)

            if resp.status_code != 200:
                return {
                    "model": model,
                    "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
                    "time_s": round(time.time() - start, 2),
                    "description": None,
                }

            data = resp.json()
            elapsed = round(time.time() - start, 2)

            if "choices" not in data or not data["choices"]:
                return {
                    "model": model,
                    "error": f"No choices: {json.dumps(data)[:300]}",
                    "time_s": elapsed,
                    "description": None,
                }

            description = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})

            return {
                "model": model,
                "description": description,
                "time_s": elapsed,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_cost": data.get("usage", {}).get("total_cost"),
                "error": None,
            }
    except Exception as e:
        return {
            "model": model,
            "error": str(e),
            "time_s": round(time.time() - start, 2),
            "description": None,
        }


async def main():
    print("=" * 80)
    print("VLM MODEL COMPARISON TEST")
    print("=" * 80)

    # Step 1: Fetch video tweets (skip first satellite one, get Trump + soldier)
    print("\n[1/3] Fetching liked tweets with video...")
    all_video_tweets = await fetch_media_tweets(count=3, video_only=True, pages=10)
    # Skip first (satellite), keep Trump and soldier
    tweets = all_video_tweets[1:3] if len(all_video_tweets) >= 3 else all_video_tweets
    print(f"  Testing {len(tweets)} video tweets")

    if not tweets:
        print("No media tweets found!")
        return

    # Step 2: Download media
    folder = Path("test_vlm_media")
    folder.mkdir(exist_ok=True)
    print("\n[2/3] Downloading media...")

    test_items = []  # (tweet, path, media_type)
    for tweet in tweets:
        paths = await download_media(tweet, folder)
        for path in paths:
            media_type = "video" if path.suffix == ".mp4" else "image"
            test_items.append((tweet, path, media_type))
            size_mb = path.stat().st_size / (1024 * 1024)
            print(f"  {path.name} ({media_type}, {size_mb:.1f}MB) — @{tweet['author_username']}")
            # Skip large videos (>15MB) to avoid timeouts
            if size_mb > 15:
                print(f"    ^ Skipping (too large for inline base64)")
                test_items.pop()

    if not test_items:
        print("No downloadable media found!")
        return

    # Step 3: Run all models on each media item
    print(f"\n[3/3] Testing {len(MODELS)} models on {len(test_items)} media items...")
    print("=" * 80)

    for item_idx, (tweet, path, media_type) in enumerate(test_items):
        print(f"\n{'─' * 80}")
        print(f"MEDIA {item_idx + 1}: {path.name} ({media_type})")
        print(f"Tweet: {tweet.get('text', '')}")
        print(f"Author: @{tweet['author_username']}")
        print(f"URL: https://x.com/{tweet['author_username']}/status/{tweet['id']}")
        print(f"{'─' * 80}")

        for model, prompt_style, extra_params in MODELS:
            short_name = model.split("/")[-1]
            temp = extra_params.get("temperature", "default")
            print(f"\n  [{short_name} / {prompt_style} / temp={temp}]")

            result = await call_vlm(model, path, media_type, tweet, prompt_style, extra_params)

            if result["error"]:
                print(f"    ERROR: {result['error'][:200]}")
                print(f"    Time: {result['time_s']}s")
            else:
                print(f"    Time: {result['time_s']}s | "
                      f"Tokens: {result.get('prompt_tokens', '?')} in / "
                      f"{result.get('completion_tokens', '?')} out")
                print(f"    Description:")
                # Indent the description
                for line in result["description"].split("\n"):
                    print(f"      {line}")

            # Small delay between models to avoid rate limiting
            await asyncio.sleep(1)

    print(f"\n{'=' * 80}")
    print("DONE")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    asyncio.run(main())
