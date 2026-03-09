import logging
import httpx
import asyncio
import base64
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

IMAGE_DESCRIPTION_PROMPT = """Describe this image in detail in English. Include:
- Main subjects and their appearance
- Setting/environment/background
- Any visible text, signs, or captions (translate to English if in another language)
- Colors, mood, and visual style
- Notable objects or elements

Always respond in English, translating any non-English text you see.
Be comprehensive but concise."""

VIDEO_DESCRIPTION_PROMPT = """Describe this video in detail in English. Include:
- Visual content: scenes, subjects, actions, settings
- Audio content: FULLY transcribe ALL speech, narration, and dialogue word-for-word (translate to English if in another language)
- Any on-screen text (translate to English if needed)
- Music or sound effects if notable
- Overall mood and style

IMPORTANT: Transcribe ALL spoken words completely - do not summarize or skip any dialogue.
For speech/dialogue, use quotation marks and attribute to speakers if identifiable.
Always respond in English, translating any non-English content.
Be comprehensive but concise."""


class GeminiClient:
    """Client for generating VLM descriptions via Google Gemini API directly."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: int = 180,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    async def describe_media(self, media_path: Path, media_type: str) -> Optional[str]:
        """Use Gemini VLM to describe image/video content."""
        if not media_path.exists():
            logger.error(f"Media file not found: {media_path}")
            return None

        with open(media_path, "rb") as f:
            media_bytes = f.read()
        media_b64 = base64.b64encode(media_bytes).decode("utf-8")

        if media_type == "image":
            mime_type = self._get_image_mime_type(media_path)
            prompt = IMAGE_DESCRIPTION_PROMPT
        elif media_type == "video":
            mime_type = self._get_video_mime_type(media_path)
            prompt = VIDEO_DESCRIPTION_PROMPT
        else:
            logger.error(f"Unknown media type: {media_type}")
            return None

        # Gemini API payload structure
        payload = {
            "contents": [
                {
                    "parts": [
                        {"inline_data": {"mime_type": mime_type, "data": media_b64}},
                        {"text": prompt},
                    ]
                }
            ],
            "generationConfig": {
                "maxOutputTokens": 1024,
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "BLOCK_NONE",
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_NONE",
                },
            ],
        }

        return await self._call_gemini(payload)

    def _get_image_mime_type(self, path: Path) -> str:
        suffix = path.suffix.lower()
        return {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(suffix, "image/jpeg")

    def _get_video_mime_type(self, path: Path) -> str:
        suffix = path.suffix.lower()
        return {
            ".mp4": "video/mp4",
            ".mov": "video/mov",
            ".avi": "video/avi",
            ".webm": "video/webm",
            ".mpg": "video/mpg",
            ".mpeg": "video/mpeg",
            ".wmv": "video/wmv",
            ".3gpp": "video/3gpp",
        }.get(suffix, "video/mp4")

    async def _call_gemini(self, payload: dict) -> Optional[str]:
        url = f"{GEMINI_API_BASE_URL}/models/{self.model}:generateContent"

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        url,
                        headers=headers,
                        json=payload,
                    )

                    # Auth errors - don't retry
                    if response.status_code in (401, 403):
                        logger.error(
                            f"Auth error ({response.status_code}): {response.text[:200]}"
                        )
                        return None

                    # Rate limit - wait and retry
                    if response.status_code == 429:
                        wait_time = self._get_retry_wait(attempt)
                        logger.warning(f"Rate limited, waiting {wait_time}s")
                        await asyncio.sleep(wait_time)
                        continue

                    response.raise_for_status()
                    data = response.json()

                    # Parse Gemini response structure
                    if "candidates" not in data or not data["candidates"]:
                        logger.warning(f"No candidates in response: {data}")
                        return None

                    candidate = data["candidates"][0]
                    if "content" not in candidate:
                        # Check for safety block or other issues
                        finish_reason = candidate.get("finishReason", "UNKNOWN")
                        logger.warning(
                            f"No content in candidate, finishReason: {finish_reason}"
                        )
                        return None

                    parts = candidate["content"].get("parts", [])
                    if not parts:
                        logger.warning("No parts in response content")
                        return None

                    # Extract text from parts
                    text_parts = [p.get("text", "") for p in parts if "text" in p]
                    return "".join(text_parts)

            except httpx.TimeoutException:
                wait_time = self._get_retry_wait(attempt)
                logger.warning(f"Timeout, retrying in {wait_time}s")
                await asyncio.sleep(wait_time)

            except Exception as e:
                if attempt < self.max_retries:
                    wait_time = self._get_retry_wait(attempt)
                    logger.warning(f"Error: {e}, retrying in {wait_time}s")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Failed after {self.max_retries} attempts: {e}")
                    return None

        return None

    def _get_retry_wait(self, attempt: int) -> float:
        return min(5 * (3 ** (attempt - 1)), 60)
