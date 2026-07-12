import asyncio
import base64
import logging
from pathlib import Path

import httpx

from social_archiver.llm._backoff import retry_wait

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
    """VLM client using the Gemini API directly (not Vertex)."""

    def __init__(self, api_key: str, model: str, timeout: int = 180, max_retries: int = 3):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    async def describe_media(
        self, media_path: Path, media_type: str, thread_context: str | None = None
    ) -> str | None:
        if not media_path.exists():
            logger.error(f"Media file not found: {media_path}")
            return None

        media_b64 = base64.b64encode(media_path.read_bytes()).decode("utf-8")

        if media_type == "image":
            mime_type = self._get_image_mime_type(media_path)
            prompt = IMAGE_DESCRIPTION_PROMPT
        elif media_type == "video":
            mime_type = self._get_video_mime_type(media_path)
            prompt = VIDEO_DESCRIPTION_PROMPT
        else:
            logger.error(f"Unknown media type: {media_type}")
            return None

        payload = {
            "contents": [{"parts": [{"inline_data": {"mime_type": mime_type, "data": media_b64}}, {"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 8192},
            "safetySettings": [
                {"category": c, "threshold": "BLOCK_NONE"}
                for c in (
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT",
                )
            ],
        }

        return await self._call_gemini(payload)

    def _get_image_mime_type(self, path: Path) -> str:
        return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".gif": "image/gif", ".webp": "image/webp"}.get(path.suffix.lower(), "image/jpeg")

    def _get_video_mime_type(self, path: Path) -> str:
        return {".mp4": "video/mp4", ".mov": "video/mov", ".avi": "video/avi", ".webm": "video/webm",
                ".mpg": "video/mpg", ".mpeg": "video/mpeg", ".wmv": "video/wmv",
                ".3gpp": "video/3gpp"}.get(path.suffix.lower(), "video/mp4")

    async def _call_gemini(self, payload: dict) -> str | None:
        url = f"{GEMINI_API_BASE_URL}/models/{self.model}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}

        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url, headers=headers, json=payload)

                    if response.status_code in (401, 403):
                        logger.error(f"Auth error ({response.status_code}): {response.text[:200]}")
                        return None

                    if response.status_code == 429:
                        wait = retry_wait(attempt)
                        logger.warning(f"Rate limited, waiting {wait}s")
                        await asyncio.sleep(wait)
                        continue

                    response.raise_for_status()
                    data = response.json()

                    candidates = data.get("candidates")
                    if not candidates:
                        logger.warning(f"No candidates in response: {data}")
                        return None

                    candidate = candidates[0]
                    if "content" not in candidate:
                        logger.warning(f"No content in candidate, finishReason: {candidate.get('finishReason', 'UNKNOWN')}")
                        return None

                    parts = candidate["content"].get("parts", [])
                    return "".join(p.get("text", "") for p in parts if "text" in p)

            except httpx.TimeoutException:
                wait = retry_wait(attempt)
                logger.warning(f"Timeout, retrying in {wait}s")
                await asyncio.sleep(wait)
            except Exception as e:
                if attempt >= self.max_retries:
                    logger.error(f"Failed after {self.max_retries} attempts: {e}")
                    return None
                wait = retry_wait(attempt)
                logger.warning(f"Error: {e}, retrying in {wait}s")
                await asyncio.sleep(wait)

        return None
