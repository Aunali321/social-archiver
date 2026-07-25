"""Long-form content extraction for tweets: X Articles.

An Article's full body rides along on the normal tweet result when the article
field toggles are set (see client.py) — no extra API calls. Ported from bird's
article renderer.

Content precedence for a tweet's text is article > note tweet > legacy full_text:
an Article's `legacy.full_text` is just a short blurb, so the rendered article
body wins when present.
"""

from typing import Any


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _build_entity_map(raw: Any) -> dict[int, dict[str, Any]]:
    """content_state.entityMap is either a list of {key,value} or a {key: value}
    object, keyed by stringified integers."""
    entity_map: dict[int, dict[str, Any]] = {}
    if isinstance(raw, list):
        for entry in raw:
            key = entry.get("key")
            if key is not None and str(key).lstrip("-").isdigit():
                entity_map[int(key)] = entry.get("value", {})
    elif isinstance(raw, dict):
        for key, value in raw.items():
            if str(key).lstrip("-").isdigit():
                entity_map[int(key)] = value
    return entity_map


def _render_block_text(block: dict[str, Any], entity_map: dict[int, dict[str, Any]]) -> str:
    """Apply inline LINK entities as markdown links. Processed back-to-front so
    earlier offsets stay valid as later ranges are rewritten."""
    text = block.get("text", "")
    ranges = [
        r
        for r in (block.get("entityRanges") or [])
        if (e := entity_map.get(r.get("key"))) and e.get("type") == "LINK" and e.get("data", {}).get("url")
    ]
    for r in sorted(ranges, key=lambda r: r["offset"], reverse=True):
        url = entity_map[r["key"]]["data"]["url"]
        start, end = r["offset"], r["offset"] + r["length"]
        text = f"{text[:start]}[{text[start:end]}]({url}){text[end:]}"
    return text.strip()


def _render_atomic_block(block: dict[str, Any], entity_map: dict[int, dict[str, Any]]) -> str | None:
    """Atomic blocks are placeholders for embedded entities (code, tweets, ...)."""
    ranges = block.get("entityRanges") or []
    if not ranges:
        return None
    entity = entity_map.get(ranges[0].get("key"))
    if not entity:
        return None
    data = entity.get("data", {})
    match entity.get("type"):
        case "MARKDOWN":
            return (data.get("markdown") or "").strip() or None
        case "DIVIDER":
            return "---"
        case "TWEET":
            tweet_id = data.get("tweetId")
            return f"[Embedded Tweet: https://x.com/i/status/{tweet_id}]" if tweet_id else None
        case "LINK":
            url = data.get("url")
            return f"[Link: {url}]" if url else None
        case "IMAGE":
            return "[Image]"
        case _:
            return None


def _render_content_state(content_state: dict[str, Any] | None) -> str | None:
    """Render a Draft.js content_state into markdown."""
    blocks = (content_state or {}).get("blocks")
    if not blocks:
        return None

    entity_map = _build_entity_map(content_state.get("entityMap") or [])
    lines: list[str] = []
    ordered_counter = 0
    previous_type: str | None = None

    for block in blocks:
        block_type = block.get("type")
        if block_type != "ordered-list-item" and previous_type == "ordered-list-item":
            ordered_counter = 0

        if block_type == "atomic":
            if entity := _render_atomic_block(block, entity_map):
                lines.append(entity)
        elif text := _render_block_text(block, entity_map):
            match block_type:
                case "header-one":
                    lines.append(f"# {text}")
                case "header-two":
                    lines.append(f"## {text}")
                case "header-three":
                    lines.append(f"### {text}")
                case "unordered-list-item":
                    lines.append(f"- {text}")
                case "ordered-list-item":
                    ordered_counter += 1
                    lines.append(f"{ordered_counter}. {text}")
                case "blockquote":
                    lines.append(f"> {text}")
                case _:
                    lines.append(text)

        previous_type = block_type

    return "\n\n".join(lines).strip() or None


def _article_result(result: dict[str, Any]) -> dict[str, Any] | None:
    """The article body carrier: article.article_results.result, requiring a
    rest_id and title (matches the web client's isArticlePost gate)."""
    article = result.get("article")
    if not isinstance(article, dict):
        return None
    inner = article.get("article_results", {}).get("result")
    if isinstance(inner, dict) and inner.get("rest_id") and _first_text(inner.get("title")):
        return inner
    return None


def is_article(result: dict[str, Any]) -> bool:
    return _article_result(result) is not None


def _body_leads_with_title(body: str, title: str) -> bool:
    """Whether a rendered body already opens with the title (as plain text or a
    markdown heading), so the title shouldn't be prepended again."""
    head, title = body.lstrip(), title.strip()
    return (
        head == title
        or head.startswith(f"{title}\n")
        or any(head.startswith(f"{level} {title}") for level in ("#", "##", "###"))
    )


def extract_article_text(result: dict[str, Any]) -> str | None:
    """Rendered article as `title\\n\\nbody`. Prefers the rich content_state,
    falling back to plain_text / preview_text when the body wasn't inlined."""
    article = _article_result(result)
    if article is None:
        return None

    title = _first_text(article.get("title"))

    rich_body = _render_content_state(article.get("content_state"))
    if rich_body:
        if title and not _body_leads_with_title(rich_body, title):
            return f"{title}\n\n{rich_body}"
        return rich_body

    body = _first_text(article.get("plain_text"), article.get("preview_text"))
    if body and title and body.strip() == title.strip():
        body = None
    if not body:
        return title
    if title and not body.startswith(title):
        return f"{title}\n\n{body}"
    return body


def extract_note_tweet_text(result: dict[str, Any]) -> str | None:
    note = result.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {})
    if not note:
        return None
    for container in (note, note.get("content", {})):
        for key in ("text", "richtext", "rich_text"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict) and _first_text(value.get("text")):
                return value["text"].strip()
    return None


def extract_tweet_text(result: dict[str, Any] | None) -> str | None:
    """Best available text for a tweet: article > note tweet > legacy full_text."""
    if not result:
        return None
    return (
        extract_article_text(result)
        or extract_note_tweet_text(result)
        or _first_text(result.get("legacy", {}).get("full_text"))
    )
