"""Shared types for VLM captioning.

The interleaved input a thread call is built from, the per-media caption schema
the model fills in, and the rich result a call returns. A result carries more
than the captions: the reasoning trace, the raw output, token usage and a
status, so a run can be graded, retried by cause, and mined later for a
fine-tuning set without re-inferring anything.

Media travels as a file path, never bytes: the archive already holds the file,
so a trace references it rather than duplicating tens of gigabytes into the
database.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str


@dataclass(frozen=True, slots=True)
class MediaPart:
    path: Path
    mime_type: str
    item_id: str
    media_index: int


type ThreadPart = TextPart | MediaPart


class MediaCaption(BaseModel):
    # `tweet_id` is the echo token every platform's label uses (see the port
    # label functions); it carries the item_id value regardless of platform.
    tweet_id: str = Field(description="The id this media belongs to, copied from the [tweet_id:...] label.")
    media_index: int = Field(description="0-based index of this media within the item (0 if only one).")
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


class VlmStatus(StrEnum):
    """Outcome of a single VLM call, and the retry policy each implies.

    SUCCESS commits. REFUSED and TRUNCATED are retryable by different means:
    a refusal on a later scheduled pass, a truncation immediately with a
    larger output budget. FAILED is a technical failure the call's own retries
    already exhausted."""

    SUCCESS = "success"
    REFUSED = "refused"  # provider blocked the prompt or the output on safety/policy/recitation
    TRUNCATED = "truncated"  # hit the output-token cap before finishing
    FAILED = "failed"  # transient or technical failure that survived the call's retries


@dataclass(frozen=True, slots=True)
class ThreadResult:
    """One thread call's outcome. `captions` is set only on SUCCESS; the rest is
    always populated so a failed or refused call is still fully recorded."""

    status: VlmStatus
    captions: ThreadCaptions | None
    reasoning: str | None
    reasoning_summary: str | None
    raw_output: str | None
    finish_reason: str | None
    usage: dict[str, int]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class MediaResult:
    """One single-media call's outcome (albums, and platforms without threads)."""

    status: VlmStatus
    description: str | None
    reasoning: str | None
    reasoning_summary: str | None
    finish_reason: str | None
    usage: dict[str, int]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class VlmTrace:
    """One captioning call, recorded whole for cost accounting and as a
    distillation dataset. `input` is the interleaved prompt with media as path
    references; `reasoning` and `output` are the model's own, unmerged."""

    platform: str
    model: str
    provider: str
    params: dict
    target_item_ids: list[str]
    input: list[dict]
    reasoning: str | None
    reasoning_summary: str | None
    output: str | None
    finish_reason: str | None
    status: VlmStatus
    usage: dict[str, int]
    error: str | None


def serialize_parts(parts: list[ThreadPart]) -> list[dict]:
    """The interleaved input as plain dicts for a trace: text verbatim, media as
    a reference (which item, which index, the on-disk path and mime)."""
    serialized: list[dict] = []
    for part in parts:
        match part:
            case TextPart(text=text):
                serialized.append({"type": "text", "text": text})
            case MediaPart(path=path, mime_type=mime, item_id=item_id, media_index=index):
                serialized.append(
                    {"type": "media", "item_id": item_id, "index": index, "path": str(path), "mime": mime}
                )
    return serialized
