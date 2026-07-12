import logging
import asyncio
import json
from typing import Optional, List, Dict
from pathlib import Path

from pydantic import BaseModel, Field
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


class MediaCaption(BaseModel):
    tweet_id: str = Field(description="The tweet ID this media belongs to.")
    media_index: int = Field(description="0-based index of this media within the tweet (0 if only one).")
    visual_description: str = Field(description="What is visually shown: scenes, people, setting, objects, layout, colors, actions, camera angles.")
    visible_text: str = Field(description="ALL text visible in the media, transcribed word-for-word exactly as it appears. Labels, headlines, chyrons, tickers, signs, watermarks, credits, dates, UI elements. Empty string if no text visible.")
    speech_transcript: str = Field(description="Complete verbatim transcript of ALL spoken words. Attribute to speakers when identifiable. Mark unclear parts as [inaudible]. Empty string if no speech or if image.")
    audio_description: str = Field(description="Background music, sound effects, ambient sounds. Empty string if silent or image.")


class ThreadCaptions(BaseModel):
    captions: List[MediaCaption] = Field(description="One caption per media item in the thread.")

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


class VertexVLMClient:
    """VLM client using Google Vertex AI (google-genai SDK)."""

    def __init__(
        self,
        model: str = "gemini-3-flash-preview",
        project: Optional[str] = None,
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
            http_options=types.HttpOptions(
                api_version="v1",
                timeout=timeout * 1000,
            ),
        )
        logger.info(f"Vertex AI client initialized: model={model}, project={project or '(auto)'}")

    async def describe_media(
        self, media_path: Path, media_type: str, thread_context: Optional[str] = None
    ) -> Optional[str]:
        """Use Gemini via Vertex AI to describe image/video content."""
        if not media_path.exists():
            logger.error(f"Media file not found: {media_path}")
            return None

        with open(media_path, "rb") as f:
            media_bytes = f.read()

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
                            safety_settings=[
                                types.SafetySetting(
                                    category="HARM_CATEGORY_HARASSMENT",
                                    threshold="OFF",
                                ),
                                types.SafetySetting(
                                    category="HARM_CATEGORY_HATE_SPEECH",
                                    threshold="OFF",
                                ),
                                types.SafetySetting(
                                    category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                                    threshold="OFF",
                                ),
                                types.SafetySetting(
                                    category="HARM_CATEGORY_DANGEROUS_CONTENT",
                                    threshold="OFF",
                                ),
                            ],
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
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    wait = self._get_retry_wait(attempt)
                    logger.warning(f"Rate limited, waiting {wait}s (attempt {attempt})")
                    await asyncio.sleep(wait)
                    continue

                if attempt < self.max_retries:
                    wait = self._get_retry_wait(attempt)
                    logger.warning(f"Error: {e}, retrying in {wait}s (attempt {attempt})")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"Failed after {self.max_retries} attempts: {e}")
                    return None

        return None

    async def describe_thread(self, parts_data: list) -> Optional[ThreadCaptions]:
        """Describe all media in a thread with interleaved text + media.

        parts_data: list of dicts, each either:
          {"type": "text", "text": "...", "tweet_id": "..."}
          {"type": "media", "path": Path, "mime_type": "image/jpeg"|..., "tweet_id": "...", "media_index": 0}

        Returns ThreadCaptions with one MediaCaption per media item.
        """
        sdk_parts = []
        for p in parts_data:
            if p["type"] == "text":
                sdk_parts.append(types.Part.from_text(text=p["text"]))
            elif p["type"] == "media":
                with open(p["path"], "rb") as f:
                    data = f.read()
                sdk_parts.append(types.Part.from_bytes(data=data, mime_type=p["mime_type"]))

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
                            safety_settings=[
                                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF"),
                                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
                            ],
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
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    wait = self._get_retry_wait(attempt)
                    logger.warning(f"Rate limited, waiting {wait}s (attempt {attempt})")
                    await asyncio.sleep(wait)
                    continue

                if attempt < self.max_retries:
                    wait = self._get_retry_wait(attempt)
                    logger.warning(f"Error: {e}, retrying in {wait}s (attempt {attempt})")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"Failed after {self.max_retries} attempts: {e}")
                    return None

        return None

    def _get_image_mime_type(self, path: Path) -> str:
        suffix = path.suffix.lower()
        return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".gif": "image/gif", ".webp": "image/webp"}.get(suffix, "image/jpeg")

    def _get_video_mime_type(self, path: Path) -> str:
        suffix = path.suffix.lower()
        return {".mp4": "video/mp4", ".mov": "video/quicktime",
                ".avi": "video/x-msvideo", ".webm": "video/webm"}.get(suffix, "video/mp4")

    def _get_retry_wait(self, attempt: int) -> float:
        return min(5 * (3 ** (attempt - 1)), 60)
