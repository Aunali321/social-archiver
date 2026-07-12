import logging
import httpx
import asyncio
import base64
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

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


class VLMClient:
    """Client for generating VLM descriptions via OpenRouter API."""

    def __init__(
        self,
        api_key: str,
        vlm_model: str,
        timeout: int = 180,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.vlm_model = vlm_model
        self.timeout = timeout
        self.max_retries = max_retries

    async def describe_media(self, media_path: Path, media_type: str, thread_context: Optional[str] = None) -> Optional[str]:
        """Use VLM to describe image/video content."""
        if not media_path.exists():
            logger.error(f"Media file not found: {media_path}")
            return None

        with open(media_path, "rb") as f:
            media_bytes = f.read()
        media_b64 = base64.b64encode(media_bytes).decode("utf-8")

        if media_type == "image":
            mime_type = self._get_image_mime_type(media_path)
            prompt = IMAGE_DESCRIPTION_PROMPT
            content = [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{media_b64}"}},
                {"type": "text", "text": prompt},
            ]
        elif media_type == "video":
            mime_type = self._get_video_mime_type(media_path)
            prompt = VIDEO_DESCRIPTION_PROMPT
            content = [
                {"type": "video_url", "video_url": {"url": f"data:{mime_type};base64,{media_b64}"}},
                {"type": "text", "text": prompt},
            ]
        else:
            logger.error(f"Unknown media type: {media_type}")
            return None

        payload = {
            "model": self.vlm_model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 8192,
        }

        return await self._call_openrouter(payload)

    def _get_image_mime_type(self, path: Path) -> str:
        suffix = path.suffix.lower()
        return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", 
                ".gif": "image/gif", ".webp": "image/webp"}.get(suffix, "image/jpeg")

    def _get_video_mime_type(self, path: Path) -> str:
        suffix = path.suffix.lower()
        return {".mp4": "video/mp4", ".mov": "video/quicktime", 
                ".avi": "video/x-msvideo", ".webm": "video/webm"}.get(suffix, "video/mp4")

    async def _call_openrouter(self, payload: dict) -> Optional[str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/insta-archiver",
        }

        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{OPENROUTER_BASE_URL}/chat/completions",
                        headers=headers,
                        json=payload,
                    )

                    if response.status_code in (401, 402, 403):
                        logger.error(f"Auth error ({response.status_code}): {response.text[:200]}")
                        return None

                    if response.status_code == 429:
                        wait_time = self._get_retry_wait(attempt)
                        logger.warning(f"Rate limited, waiting {wait_time}s")
                        await asyncio.sleep(wait_time)
                        continue

                    response.raise_for_status()
                    data = response.json()

                    if "choices" not in data or not data["choices"]:
                        logger.warning(f"Unexpected response: {data}")
                        raise KeyError("choices")

                    return data["choices"][0]["message"]["content"]

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
