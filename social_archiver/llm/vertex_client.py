"""VLM client using Google Vertex AI (google-genai SDK).

Captions archived media on Gemini. One thread call describes every media item in
a conversation at once, using the surrounding text as context, and returns a
structured caption per item. Every call also returns its reasoning trace and a
status, so a run is graded and retried by cause and the outputs feed a later
fine-tuning set.

Media is sent inline. Vertex accepts a request payload up to 500 MB, so all but
a handful of very long videos caption directly, without a staging bucket.

The reasoning trace comes back one of two ways. `includeThoughts` returns what
the provider *summarizes* the thinking as, which reads retrospectively and is not
the model's own words. Raw mode instead turns native thinking off and forces a
`think` tool, leaving the model nowhere to reason but the tool arguments, which
come back as plaintext — its actual working trace. That costs a second turn (the
forced call produces reasoning but no answer), and only works on models that
honour `thinkingBudget=0`: measured, 3.5 Flash and 3.1 Flash Lite do, 3.7 Flash
floors at ~85 thought tokens and yields a plan, and 2.5 Pro rejects the setting
outright. `thoughtsTokenCount` on the reply is the check, so a trace is never
recorded as raw when thinking leaked.
"""

import asyncio
import logging
import warnings

from google import genai
from google.genai import errors, types

from social_archiver.llm._backoff import retry_wait
from social_archiver.llm.vlm_types import (
    MediaCaption,
    MediaPart,
    MediaResult,
    TextPart,
    ThreadCaptions,
    ThreadPart,
    ThreadResult,
    VlmStatus,
)

logger = logging.getLogger(__name__)

# Re-exported so callers keep importing the part/caption types from the client.
__all__ = [
    "MediaCaption",
    "MediaPart",
    "MediaResult",
    "TextPart",
    "ThreadCaptions",
    "ThreadPart",
    "ThreadResult",
    "VertexVLMClient",
    "VlmStatus",
]

# The SDK's ServiceTier enum predates the Vertex tier names and warns on the raw
# value even though the request succeeds; the value is what Vertex requires.
warnings.filterwarnings("ignore", message=".*is not a valid ServiceTier.*", category=UserWarning)

# finishReason values that mean the model (or the safety layer) declined to
# answer this content, as opposed to a technical failure. Retriable later with
# safety off and, for some, never; either way not a bug to fix now.
_REFUSAL_FINISH_REASONS = frozenset(
    {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}
)

_THINK_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="think",
            description="Private scratchpad. Write your COMPLETE step-by-step reasoning here before answering.",
            parameters={
                "type": "object",
                "properties": {"thoughts": {"type": "string"}},
                "required": ["thoughts"],
            },
        )
    ]
)
# ANY with a single allowed name is what forces the call; AUTO on the second turn
# lets the model answer instead of reaching for the tool again.
_FORCE_THINK = types.ToolConfig(
    function_calling_config=types.FunctionCallingConfig(mode="ANY", allowed_function_names=["think"])
)
_ANSWER = types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="AUTO"))

_SAFETY_SETTINGS = [
    types.SafetySetting(category=category, threshold="OFF")
    for category in (
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]

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

THREAD_DESCRIPTION_PROMPT = """You are an archival media captioner producing highly detailed descriptions for future search and retrieval. Above is a thread of posts with interleaved media. Each post is labeled with its id in brackets like [tweet_id:123456].

For EACH media item, produce an exhaustive archival description. Use the tweet_id and media_index (0-based) from the label preceding each media.

USE CONTEXT — the post text, thread, and quoted posts tell you WHAT you're looking at:
- Name specific people, places, events, organizations, and topics when the surrounding text identifies them.
- Use the post and thread text to understand the subject matter, then describe the media in that context.
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
    """Captions media on Gemini via Vertex AI. Concurrency is the caller's to
    bound; a single client instance is safe to call from many tasks at once."""

    def __init__(
        self,
        model: str,
        project: str | None = None,
        location: str = "global",
        service_tier: str = "SERVICE_TIER_FLEX",
        capture_reasoning: bool = True,
        raw_reasoning: bool = False,
        max_output_tokens: int = 65536,
        timeout: int = 900,
        max_retries: int = 3,
    ):
        self.model = model
        self.service_tier = service_tier
        self.max_output_tokens = max_output_tokens
        self.max_retries = max_retries
        self.raw_reasoning = raw_reasoning
        # Raw mode needs the hidden channel closed, so the model's only place to
        # reason is the forced tool call. Otherwise thinking on captures the
        # provider's summary alongside the caption; off is cheaper when only the
        # caption is wanted.
        self.thinking = (
            types.ThinkingConfig(thinking_budget=0)
            if raw_reasoning or not capture_reasoning
            else types.ThinkingConfig(include_thoughts=True)
        )
        self.client = genai.Client(
            vertexai=True,
            location=location,
            **({"project": project} if project else {}),
            http_options=types.HttpOptions(api_version="v1", timeout=timeout * 1000),
        )
        logger.info(
            f"Vertex VLM initialized: model={model}, tier={service_tier}, "
            f"reasoning={self._reasoning_mode}, project={project or '(auto)'}"
        )

    @property
    def _reasoning_mode(self) -> str:
        if self.raw_reasoning:
            return "raw"
        return "summary" if self.thinking.include_thoughts else "off"

    @property
    def provider(self) -> str:
        return "vertex"

    @property
    def params(self) -> dict:
        """The generation settings recorded on every trace, so a dataset split
        mid-run stays interpretable."""
        return {
            "service_tier": self.service_tier,
            "max_output_tokens": self.max_output_tokens,
            "thinking": self._reasoning_mode,
            "safety": "off",
        }

    def _config(self, **overrides) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=self.max_output_tokens,
            service_tier=self.service_tier,
            thinking_config=self.thinking,
            safety_settings=_SAFETY_SETTINGS,
            **overrides,
        )

    async def describe_thread(self, parts: list[ThreadPart]) -> ThreadResult:
        """Describe all media in a thread of interleaved text and media parts.
        Text parts carry the [tweet_id:...] labels the model echoes back in each
        caption, so no other bookkeeping travels with the media."""
        sdk_parts = [_to_sdk_part(part) for part in parts]
        sdk_parts.append(types.Part.from_text(text=THREAD_DESCRIPTION_PROMPT))

        outcome = await self._answer(
            sdk_parts,
            response_mime_type="application/json",
            response_json_schema=ThreadCaptions.model_json_schema(),
        )
        if outcome.status is not VlmStatus.SUCCESS:
            return ThreadResult(outcome.status, None, outcome.reasoning, outcome.text, outcome.finish_reason, outcome.usage, outcome.error)

        try:
            captions = ThreadCaptions.model_validate_json(outcome.text or "")
        except Exception as e:
            # A well-formed 200 whose body is not the promised schema is a
            # technical failure, not a refusal: the content came through.
            return ThreadResult(VlmStatus.FAILED, None, outcome.reasoning, outcome.text, outcome.finish_reason, outcome.usage, f"caption parse failed: {e}")

        logger.info(f"Thread captioned: {len(captions.captions)} media items, finish={outcome.finish_reason}")
        return ThreadResult(VlmStatus.SUCCESS, captions, outcome.reasoning, outcome.text, outcome.finish_reason, outcome.usage, None)

    async def describe_media(self, path, media_type: str, thread_context: str | None = None) -> MediaResult:
        """Describe a single image or video. Used where there is no thread to
        batch (albums, and platforms without a reply graph)."""
        prompt = IMAGE_DESCRIPTION_PROMPT if media_type == "image" else VIDEO_DESCRIPTION_PROMPT
        if thread_context:
            prompt = f"{thread_context}\n\n{prompt}"

        sdk_parts = [
            types.Part.from_bytes(data=path.read_bytes(), mime_type=_mime_for(path, media_type)),
            types.Part.from_text(text=prompt),
        ]
        outcome = await self._answer(sdk_parts)
        description = (outcome.text or "").strip() or None
        if outcome.status is VlmStatus.SUCCESS and not description:
            return MediaResult(VlmStatus.FAILED, None, outcome.reasoning, outcome.finish_reason, outcome.usage, "empty description")
        return MediaResult(outcome.status, description, outcome.reasoning, outcome.finish_reason, outcome.usage, outcome.error)

    async def _answer(self, sdk_parts: list[types.Part], **overrides) -> "_Outcome":
        """The call that produces the caption, with whatever reasoning capture is
        configured. Summary mode is one call; raw mode spends a first turn on the
        forced `think` tool, because a forced call yields reasoning and no answer."""
        if not self.raw_reasoning:
            return await self._generate([types.Content(role="user", parts=sdk_parts)], self._config(**overrides))

        contents = [types.Content(role="user", parts=sdk_parts)]
        first = await self._call(contents, self._config(tools=[_THINK_TOOL], tool_config=_FORCE_THINK))
        if isinstance(first, _Outcome):
            return first  # the think turn never completed; nothing to answer from

        reasoning, leaked = _read_think(first)
        if leaked:
            logger.warning(f"Native thinking leaked {leaked} tokens; the captured trace is a plan, not the reasoning")

        contents.append(first.candidates[0].content)
        contents.append(
            types.Content(role="user", parts=[types.Part.from_function_response(name="think", response={"recorded": True})])
        )
        outcome = await self._generate(contents, self._config(tools=[_THINK_TOOL], tool_config=_ANSWER, **overrides))
        # Both turns bill, so the trace carries their sum rather than the answer's alone.
        return _Outcome(
            outcome.status,
            outcome.text,
            reasoning if not leaked else outcome.reasoning or reasoning,
            outcome.finish_reason,
            _merge_usage(_usage(first), outcome.usage),
            outcome.error,
        )

    async def _generate(self, contents: list[types.Content], config: types.GenerateContentConfig) -> "_Outcome":
        response = await self._call(contents, config)
        return response if isinstance(response, _Outcome) else _read_response(response)

    async def _call(
        self, contents: list[types.Content], config: types.GenerateContentConfig
    ) -> "types.GenerateContentResponse | _Outcome":
        """One generate call with transient-failure retries. Returns the raw
        response, or an `_Outcome` when the call could not be completed at all;
        refusals and truncations are outcomes read from a successful reply."""
        for attempt in range(1, self.max_retries + 1):
            try:
                return await self.client.aio.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
            except (errors.ServerError, errors.APIError) as e:
                if not _is_transient(e) or attempt >= self.max_retries:
                    logger.error(f"VLM call failed ({_code(e)}): {e}")
                    return _Outcome(VlmStatus.FAILED, None, None, None, {}, str(e))
                wait = retry_wait(attempt)
                logger.warning(f"VLM call transient error ({_code(e)}), retrying in {wait}s (attempt {attempt})")
                await asyncio.sleep(wait)
            except TimeoutError as e:
                if attempt >= self.max_retries:
                    return _Outcome(VlmStatus.FAILED, None, None, None, {}, f"timeout: {e}")
                wait = retry_wait(attempt)
                logger.warning(f"VLM call timeout, retrying in {wait}s (attempt {attempt})")
                await asyncio.sleep(wait)

        return _Outcome(VlmStatus.FAILED, None, None, None, {}, "retries exhausted")


class _Outcome:
    """The generic result of a call, before schema validation: status, the
    answer text, the reasoning, finish reason and usage."""

    __slots__ = ("status", "text", "reasoning", "finish_reason", "usage", "error")

    def __init__(self, status, text, reasoning, finish_reason, usage, error):
        self.status = status
        self.text = text
        self.reasoning = reasoning
        self.finish_reason = finish_reason
        self.usage = usage
        self.error = error


def _read_think(response) -> tuple[str | None, int]:
    """The forced call's arguments are the trace. `thoughtsTokenCount` is the
    check that native thinking really stayed off: non-zero means the model kept
    reasoning privately and wrote only a plan into the tool."""
    for part in getattr(response.candidates[0].content, "parts", None) or []:
        call = getattr(part, "function_call", None)
        if call and call.name == "think":
            return (call.args or {}).get("thoughts"), _thought_tokens(response)
    return None, _thought_tokens(response)


def _thought_tokens(response) -> int:
    meta = getattr(response, "usage_metadata", None)
    return getattr(meta, "thoughts_token_count", None) or 0 if meta else 0


def _merge_usage(first: dict[str, int], second: dict[str, int]) -> dict[str, int]:
    merged = dict(second)
    for key, value in first.items():
        merged[key] = merged.get(key, 0) + value
    return merged


def _read_response(response) -> _Outcome:
    usage = _usage(response)

    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        reason = _name(feedback.block_reason)
        return _Outcome(VlmStatus.REFUSED, None, None, f"prompt_blocked:{reason}", usage, f"prompt blocked: {reason}")

    candidates = response.candidates or []
    if not candidates:
        return _Outcome(VlmStatus.FAILED, None, None, None, usage, "no candidates returned")

    candidate = candidates[0]
    finish = _name(candidate.finish_reason)
    parts = getattr(candidate.content, "parts", None) or []
    reasoning = "".join(p.text for p in parts if getattr(p, "thought", False) and p.text) or None
    answer = "".join(p.text for p in parts if not getattr(p, "thought", False) and p.text) or None

    if finish in _REFUSAL_FINISH_REASONS:
        return _Outcome(VlmStatus.REFUSED, answer, reasoning, finish, usage, f"blocked: {finish}")
    if finish == "MAX_TOKENS":
        return _Outcome(VlmStatus.TRUNCATED, answer, reasoning, finish, usage, "output truncated at token cap")
    if not answer:
        return _Outcome(VlmStatus.FAILED, None, reasoning, finish, usage, f"empty answer (finish={finish})")

    return _Outcome(VlmStatus.SUCCESS, answer, reasoning, finish, usage, None)


def _usage(response) -> dict[str, int]:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return {}
    usage = {
        "prompt_tokens": meta.prompt_token_count or 0,
        "output_tokens": meta.candidates_token_count or 0,
        "thoughts_tokens": meta.thoughts_token_count or 0,
        "total_tokens": meta.total_token_count or 0,
    }
    for detail in getattr(meta, "prompt_tokens_details", None) or []:
        modality = _name(getattr(detail, "modality", None))
        if modality:
            usage[f"prompt_{modality.lower()}_tokens"] = detail.token_count or 0
    return usage


def _to_sdk_part(part: ThreadPart) -> types.Part:
    match part:
        case TextPart(text=text):
            return types.Part.from_text(text=text)
        case MediaPart(path=path, mime_type=mime_type):
            return types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type)


def _mime_for(path, media_type: str) -> str:
    from social_archiver.core.media_kind import guess_mime_type

    return guess_mime_type(path) or ("image/jpeg" if media_type == "image" else "video/mp4")


def _name(value) -> str | None:
    """The bare name of an SDK enum, or the value itself if already a string."""
    if value is None:
        return None
    return getattr(value, "name", None) or str(value)


def _code(error: Exception) -> str:
    return str(getattr(error, "code", "") or "")


def _is_transient(error: Exception) -> bool:
    code = _code(error)
    return code in ("429", "500", "502", "503", "504")
