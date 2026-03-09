import logging
import httpx
import asyncio
import base64
from typing import List, Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Prompts for VLM description
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


class EmbeddingClient:
    """Client for generating embeddings via OpenRouter API (Gemini + Qwen3)"""

    def __init__(
        self,
        api_key: str,
        vlm_model: str,
        embedding_model: str,
        dimension: int,
        timeout: int = 180,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.vlm_model = vlm_model
        self.embedding_model = embedding_model
        self.dimension = dimension
        self.timeout = timeout
        self.max_retries = max_retries

    async def generate_embedding(
        self,
        media_path: Optional[Path] = None,
        text: Optional[str] = None,
        media_type: Optional[str] = None,
    ) -> Tuple[Optional[List[float]], Optional[str]]:
        """
        Generate embedding for media (image/video) or text.

        For media: First describes it using VLM, then embeds the description.
        For text-only: Directly embeds the text.

        Args:
            media_path: Path to image or video file
            text: Optional caption text (combined with VLM description for media)
            media_type: "image" or "video" if media_path provided

        Returns:
            Tuple of (embedding vector, vlm_description) or (None, None) if failed
        """
        try:
            if media_path and media_type:
                # Step 1: Get VLM description of the media
                description = await self._describe_media(media_path, media_type)
                if not description:
                    logger.error("Failed to get VLM description")
                    return None, None

                # Step 2: Combine caption with description
                text_to_embed = self._combine_caption_and_description(text, description)

                # Step 3: Generate text embedding
                embedding = await self._generate_text_embedding(text_to_embed)
                return embedding, description

            elif text:
                # Text-only embedding (no VLM description)
                embedding = await self._generate_text_embedding(text)
                return embedding, None
            else:
                logger.warning("No valid input for embedding generation")
                return None, None

        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            return None, None

    def _combine_caption_and_description(
        self, caption: Optional[str], description: str
    ) -> str:
        """Combine Instagram caption with VLM description"""
        parts = []

        if caption and caption.strip():
            parts.append(f"Caption: {caption.strip()}")

        parts.append(f"Visual: {description}")

        return "\n\n".join(parts)

    async def _describe_media(self, media_path: Path, media_type: str) -> Optional[str]:
        """Use VLM (Gemini) to describe image/video content"""

        if not media_path.exists():
            logger.error(f"Media file not found: {media_path}")
            return None

        # Read and encode media
        with open(media_path, "rb") as f:
            media_bytes = f.read()
        media_b64 = base64.b64encode(media_bytes).decode("utf-8")

        # Determine MIME type and prompt
        if media_type == "image":
            mime_type = self._get_image_mime_type(media_path)
            prompt = IMAGE_DESCRIPTION_PROMPT
            content = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{media_b64}"},
                },
                {"type": "text", "text": prompt},
            ]
        elif media_type == "video":
            mime_type = self._get_video_mime_type(media_path)
            prompt = VIDEO_DESCRIPTION_PROMPT
            content = [
                {
                    "type": "video_url",
                    "video_url": {"url": f"data:{mime_type};base64,{media_b64}"},
                },
                {"type": "text", "text": prompt},
            ]
        else:
            logger.error(f"Unknown media type: {media_type}")
            return None

        payload = {
            "model": self.vlm_model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 1024,
        }

        return await self._call_openrouter_chat(payload)

    def _get_image_mime_type(self, path: Path) -> str:
        """Get MIME type for image based on extension"""
        suffix = path.suffix.lower()
        mime_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        return mime_types.get(suffix, "image/jpeg")

    def _get_video_mime_type(self, path: Path) -> str:
        """Get MIME type for video based on extension"""
        suffix = path.suffix.lower()
        mime_types = {
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".avi": "video/x-msvideo",
            ".webm": "video/webm",
        }
        return mime_types.get(suffix, "video/mp4")

    async def _call_openrouter_chat(self, payload: dict) -> Optional[str]:
        """Make chat completion request to OpenRouter with retry logic"""

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

                    # Don't retry on payment/auth errors - they won't resolve
                    if response.status_code in (401, 402, 403):
                        logger.error(
                            f"Authentication/payment error ({response.status_code}): {response.text[:200]}"
                        )
                        return None

                    if response.status_code == 429:
                        # Rate limited - wait and retry
                        wait_time = self._get_retry_wait(attempt)
                        logger.warning(
                            f"Rate limited, waiting {wait_time}s (attempt {attempt}/{self.max_retries})"
                        )
                        await asyncio.sleep(wait_time)
                        continue

                    response.raise_for_status()
                    data = response.json()

                    # Defensive check for expected response structure
                    if "choices" not in data or not data["choices"]:
                        logger.warning(
                            f"Unexpected response format (no 'choices'): {data}"
                        )
                        raise KeyError("choices")

                    return data["choices"][0]["message"]["content"]

            except httpx.TimeoutException:
                wait_time = self._get_retry_wait(attempt)
                logger.warning(
                    f"Timeout, retrying in {wait_time}s (attempt {attempt}/{self.max_retries})"
                )
                await asyncio.sleep(wait_time)

            except Exception as e:
                if attempt < self.max_retries:
                    wait_time = self._get_retry_wait(attempt)
                    logger.warning(
                        f"Error: {e}, retrying in {wait_time}s (attempt {attempt}/{self.max_retries})"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Failed after {self.max_retries} attempts: {e}")
                    return None

        return None

    async def _generate_text_embedding(self, text: str) -> Optional[List[float]]:
        """Generate embedding for text using Qwen3 embedding model via OpenRouter"""

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/insta-archiver",
        }

        payload = {
            "model": self.embedding_model,
            "input": text,
        }

        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{OPENROUTER_BASE_URL}/embeddings",
                        headers=headers,
                        json=payload,
                    )

                    # Don't retry on payment/auth errors - they won't resolve
                    if response.status_code in (401, 402, 403):
                        logger.error(
                            f"Authentication/payment error ({response.status_code}): {response.text[:200]}"
                        )
                        return None

                    if response.status_code == 429:
                        wait_time = self._get_retry_wait(attempt)
                        logger.warning(
                            f"Rate limited, waiting {wait_time}s (attempt {attempt}/{self.max_retries})"
                        )
                        await asyncio.sleep(wait_time)
                        continue

                    response.raise_for_status()
                    data = response.json()

                    embedding = data["data"][0]["embedding"]

                    if len(embedding) != self.dimension:
                        logger.warning(
                            f"Expected {self.dimension} dimensions, got {len(embedding)}"
                        )

                    return embedding

            except httpx.TimeoutException:
                wait_time = self._get_retry_wait(attempt)
                logger.warning(
                    f"Timeout, retrying in {wait_time}s (attempt {attempt}/{self.max_retries})"
                )
                await asyncio.sleep(wait_time)

            except Exception as e:
                if attempt < self.max_retries:
                    wait_time = self._get_retry_wait(attempt)
                    logger.warning(
                        f"Error: {e}, retrying in {wait_time}s (attempt {attempt}/{self.max_retries})"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Failed after {self.max_retries} attempts: {e}")
                    return None

        return None

    def _get_retry_wait(self, attempt: int) -> float:
        """Calculate exponential backoff wait time"""
        # 5s, 15s, 45s for attempts 1, 2, 3
        return min(5 * (3 ** (attempt - 1)), 60)
