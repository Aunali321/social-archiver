import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from social_archiver.llm._backoff import retry_wait

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str


@dataclass(frozen=True, slots=True)
class MediaPart:
    path: Path
    mime_type: str


type ThreadPart = TextPart | MediaPart


class MediaCaption(BaseModel):
    tweet_id: str = Field(description="The tweet ID this media belongs to.")
    media_index: int = Field(description="0-based index of this media within the tweet (0 if only one).")
    visual_description: str = Field(
        description="What is visually shown: scenes, people, setting, objects, layout, colors, actions, camera angles."
    )
    visible_text: str = Field(
        description="ALL text visible in the media, transcribed word-for-word exactly as it appears. Labels, headlines, chyrons, tickers, signs, watermarks, credits, dates, UI elements. Empty string if no text visible."
    )
    speech_transcript: str = Field(
        description="Complete verbatim transcript of ALL spoken words. Attribute to speakers when identifiable. Mark unclear parts as [inaudible]. Empty string if no speech or if image."
    )
    audio_description: str = Field(
        description="Background music, sound effects, ambient sounds. Empty string if silent or image."
    )


class ThreadCaptions(BaseModel):
    captions: list[MediaCaption] = Field(description="One caption per media item in the thread.")


IMAGE_DESCRIPTION_PROMPT = """You are an archival media captioner. Describe ONLY what is visually present.

Rules:
- Describe what you SEE. Never infer or fabricate details not visible in the image.
- Translate all non-English text you can read in the image to English.
- Do not start with "This image shows" or "Based on the image". Just describe directly.
- No markdown headers. Write in flowing paragraphs.
- Be detailed and thorough.

Describe: subjects, setting, all visible text (translated), colors, objects, visual style."""

VIDEO_DESCRIPTION_PROMPT = """You are an archival media captioner. Describe ONLY what you can see and hear.

CRITICAL RULES:
- For speech: ONLY transcribe words you can actually hear. If audio is unclear, say "inaudible" or "unclear". NEVER guess or fabricate dialogue.
- For on-screen text: ONLY transcribe text you can actually read in the video frames.
- Do not start with "This video shows". Just describe directly.
- No markdown headers. Write in flowing paragraphs.
- Be detailed and thorough.

Describe: visual scenes, subjects, actions, setting, all audible speech (transcribed verbatim), on-screen text (translated), music/sounds, mood."""

THREAD_DESCRIPTION_PROMPT = """You are an archival media captioner producing highly detailed descriptions for future search and retrieval. Above is a thread of tweets with interleaved media. Each tweet is labeled with its tweet_id in brackets like [tweet_id:123456].

For EACH media item, produce an exhaustive archival description. Use the tweet_id and media_index (0-based) from the label preceding each media.

USE CONTEXT — the tweet text, thread, and quoted tweets tell you WHAT you're looking at:
- Name specific people, places, events, organizations, and topics when the surrounding text identifies them.
- Use the tweet and thread text to understand the subject matter, then describe the media in that context.
- A generic image becomes meaningful when you know what the thread is about — always connect the two.

FOR IMAGES — be exhaustive:
- Transcribe ALL visible text WORD FOR WORD: labels, legends, watermarks, credits, dates, coordinates, headlines, captions, signs, buttons, UI elements. Do not summarize or paraphrase any text — copy it exactly as it appears.
- Describe layout, colors, annotations, insets, arrows, highlights, overlays.
- Identify people by name if the context tells you who they are.
- Describe clothing, expressions, gestures, background objects, setting.
- For maps/charts/infographics: explain what the data shows, not just what shapes you see.

FOR VIDEOS — capture everything:
- Transcribe ALL speech VERBATIM and COMPLETELY. Do not summarize or paraphrase. Include every word spoken, even if repetitive or stumbling. Use quotation marks.
- If multiple speakers, attribute each quote.
- Describe ALL on-screen text: chyrons, tickers, lower thirds, titles, captions, watermarks.
- Describe the visual scene: setting, people, actions, camera angles, transitions.
- Describe audio: background music, sound effects, ambient sounds, silence.
- Note audio quality issues: if speech is muffled, overlapping, or inaudible, say so explicitly.

RULES:
- Ground descriptions in what you SEE and HEAR. Use context to inform, never to fabricate.
- For speech: ONLY transcribe words you can actually hear. If unclear, write "[inaudible]". NEVER guess dialogue.
- Translate all non-English visible/audible text to English, noting the original language.
- Write in flowing paragraphs. No markdown headers."""

_SAFETY_SETTINGS = [
    types.SafetySetting(category=category, threshold="OFF")
    for category in (
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]


class VertexVLMClient:
    """VLM client using Google Vertex AI (google-genai SDK)."""

    def __init__(
        self,
        model: str = "gemini-3-flash-preview",
        project: str | None = None,
        location: str = "global",
        timeout: int = 900,
        max_retries: int = 3,
    ):
        self.model = model
        self.max_retries = max_retries
        self.client = genai.Client(
            vertexai=True,
            location=location,
            **({"project": project} if project else {}),
            http_options=types.HttpOptions(api_version="v1", timeout=timeout * 1000),
        )
        logger.info(f"Vertex AI client initialized: model={model}, project={project or '(auto)'}")

    async def describe_media(self, media_path: Path, media_type: str, thread_context: str | None = None) -> str | None:
        """Describe a single image/video with Gemini."""
        if not media_path.exists():
            logger.error(f"Media file not found: {media_path}")
            return None

        media_bytes = media_path.read_bytes()

        if media_type == "image":
            mime_type = self._get_image_mime_type(media_path)
            prompt = IMAGE_DESCRIPTION_PROMPT
        elif media_type == "video":
            mime_type = self._get_video_mime_type(media_path)
            prompt = VIDEO_DESCRIPTION_PROMPT
        else:
            logger.error(f"Unknown media type: {media_type}")
            return None

        if thread_context:
            prompt = f"{thread_context}\n\n{prompt}"

        for attempt in range(1, self.max_retries + 1):
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self.client.models.generate_content(
                        model=self.model,
                        contents=[
                            types.Content(
                                role="user",
                                parts=[
                                    types.Part.from_bytes(data=media_bytes, mime_type=mime_type),
                                    types.Part.from_text(text=prompt),
                                ],
                            )
                        ],
                        config=types.GenerateContentConfig(
                            temperature=0,
                            max_output_tokens=16384,
                            safety_settings=_SAFETY_SETTINGS,
                        ),
                    ),
                )

                text = (response.text or "").strip()
                if not text:
                    logger.warning(f"Empty response for {media_path.name} (attempt {attempt})")
                    if attempt < self.max_retries:
                        await asyncio.sleep(self._get_retry_wait(attempt))
                        continue
                    return None

                return text

            except Exception as e:
                if not await self._handle_retry(e, attempt):
                    return None

        return None

    async def describe_thread(self, parts: Sequence[ThreadPart]) -> ThreadCaptions | None:
        """Describe all media in a thread of interleaved text and media parts.
        Text parts carry the [tweet_id:...] labels the model echoes back in
        each caption, so no other bookkeeping travels with the media."""
        sdk_parts = []
        for part in parts:
            match part:
                case TextPart(text=text):
                    sdk_parts.append(types.Part.from_text(text=text))
                case MediaPart(path=path, mime_type=mime_type):
                    sdk_parts.append(types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type))

        sdk_parts.append(types.Part.from_text(text=THREAD_DESCRIPTION_PROMPT))

        for attempt in range(1, self.max_retries + 1):
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self.client.models.generate_content(
                        model=self.model,
                        contents=[types.Content(role="user", parts=sdk_parts)],
                        config=types.GenerateContentConfig(
                            temperature=0,
                            max_output_tokens=16384,
                            response_mime_type="application/json",
                            response_json_schema=ThreadCaptions.model_json_schema(),
                            safety_settings=_SAFETY_SETTINGS,
                        ),
                    ),
                )

                text = (response.text or "").strip()
                if not text:
                    logger.warning(f"Empty response for thread (attempt {attempt})")
                    if attempt < self.max_retries:
                        await asyncio.sleep(self._get_retry_wait(attempt))
                        continue
                    return None

                result = ThreadCaptions.model_validate_json(text)
                logger.info(f"Got {len(result.captions)} media captions from VLM")
                return result

            except Exception as e:
                if not await self._handle_retry(e, attempt):
                    return None

        return None

    async def _handle_retry(self, error: Exception, attempt: int) -> bool:
        """Sleep and return True if this attempt should be retried."""
        err_str = str(error)
        is_rate_limit = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
        if not is_rate_limit and attempt >= self.max_retries:
            logger.error(f"Failed after {self.max_retries} attempts: {error}")
            return False

        wait = retry_wait(attempt)
        logger.warning(
            f"{'Rate limited' if is_rate_limit else f'Error: {error}'}, retrying in {wait}s (attempt {attempt})"
        )
        await asyncio.sleep(wait)
        return True

    def _get_image_mime_type(self, path: Path) -> str:
        return {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(path.suffix.lower(), "image/jpeg")

    def _get_video_mime_type(self, path: Path) -> str:
        return {".mp4": "video/mp4", ".mov": "video/quicktime", ".avi": "video/x-msvideo", ".webm": "video/webm"}.get(
            path.suffix.lower(), "video/mp4"
        )
