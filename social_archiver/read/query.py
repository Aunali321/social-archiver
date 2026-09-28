"""Search input: free text plus inline operators, e.g.

    from:elonmusk has:video after:2025-01-01 -is:retweet "exact phrase" rust OR go

Operators narrow the caller's filters and win where both set the same field. The rest
becomes an FTS5 expression: every word must match, "quoted phrases" match in order, word*
matches a prefix, -word excludes, OR joins alternatives and NEAR(a b, 5) keeps words
within five tokens of each other. Each term is quoted, so punctuation ("c++",
"self-hosted") stays text and never parses as query syntax. A token that looks like an
operator but names none (a URL's "https:") is text too.
"""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from social_archiver.core.config import PLATFORMS
from social_archiver.read.models import ItemFilters, ItemKind, MatchField, MediaFilter, PostFormat

_TOKEN = re.compile(r'-?(?:NEAR\([^)]*\)|[A-Za-z]+:"[^"]*"?|"[^"]*"?|\S+)')
_OPERATOR = re.compile(r"([A-Za-z]+):(.*)", re.DOTALL)
_NEAR = re.compile(r"NEAR\((.*)\)", re.DOTALL)

_MATCH_COLUMNS = {
    MatchField.TEXT: "text",
    MatchField.MEDIA: "vlm_description",
    MatchField.NAMES: "author_username chat_name",
}
_KINDS = {
    "post": ItemKind.POST,
    "reply": ItemKind.REPLY,
    "comment": ItemKind.REPLY,
    "retweet": ItemKind.REPOST,
    "repost": ItemKind.REPOST,
    "quote": ItemKind.QUOTE,
}
_MEDIA = {**{kind.value: kind for kind in MediaFilter}, "photo": MediaFilter.IMAGE}
_FORMATS = {"reel": PostFormat.REEL, "carousel": PostFormat.CAROUSEL}
_CATEGORIES = ("group", "dm")
_NEGATABLE = ("is", "has")
_OPERATORS = (
    "from", "in", "r", "subreddit", "platform", "category", "origin", "collection", "shared",
    "after", "before", "likes", "views", "has", "is", "match",
)  # fmt: skip


@dataclass(slots=True)
class ParsedQuery:
    expression: str | None  # FTS5; None when the input is operators alone
    text: str | None  # the free words, for archives without an FTS index and for embedding
    filters: ItemFilters


def _quote(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def _choice[T](operator: str, value: str, options: dict[str, T]) -> T:
    if value.lower() not in options:
        raise ValueError(f"{operator}:{value} is not an option; use one of {', '.join(options)}")
    return options[value.lower()]


def _number(operator: str, value: str) -> int:
    try:
        return int(value.lstrip(">="))
    except ValueError:
        raise ValueError(f"{operator}: takes a number, as in {operator}:100 or {operator}:>100") from None


def _date(operator: str, value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{operator}: takes a date, as in {operator}:2025-01-31") from None


def _operator(key: str, value: str, negated: bool, changes: dict[str, Any], excluded: list[ItemKind]):
    if negated and key not in _NEGATABLE:
        raise ValueError(f"-{key}: is not supported; only is: and has: can be negated")
    match key:
        case "from":
            changes["author"] = value.lstrip("@")
        case "in":
            changes["chat_name"] = value
        case "r" | "subreddit":
            changes["subreddit"] = value.removeprefix("r/")
        case "platform":
            changes["platforms"] = (_choice(key, value, {p: p for p in PLATFORMS}),)
        case "category" | "origin" | "collection":
            changes[key] = value
        case "shared":
            changes["shared_by"] = value.lstrip("@")
        case "after":
            changes["date_from"] = _date(key, value)
        case "before":
            changes["date_to"] = _date(key, value)
        case "likes":
            changes["min_likes"] = _number(key, value)
        case "views":
            changes["min_views"] = _number(key, value)
        case "match":
            changes["match"] = _choice(key, value, {f.value: f for f in MatchField})
        case "has":
            wanted = value.lower()
            if wanted in ("media", "link"):
                changes["has_media" if wanted == "media" else "has_link"] = not negated
            elif negated:
                raise ValueError(f"-has:{value} is not supported; -has: takes media or link")
            elif wanted in _MEDIA:
                changes["media"] = _MEDIA[wanted]
            else:
                raise ValueError(f"has:{value} is not an option; use one of media, link, {', '.join(_MEDIA)}")
        case "is":
            wanted = value.lower()
            if wanted in _KINDS and negated:
                excluded.append(_KINDS[wanted])
            elif wanted in _KINDS:
                changes["kind"] = _KINDS[wanted]
            elif negated:
                raise ValueError(f"-is:{value} is not supported; negate {', '.join(_KINDS)}")
            elif wanted in _FORMATS:
                changes["post_format"] = _FORMATS[wanted]
            elif wanted in _CATEGORIES:
                changes["category"] = wanted
            elif wanted == "liked":
                changes["seeds_only"] = True
            else:
                options = [*_KINDS, *_FORMATS, *_CATEGORIES, "liked"]
                raise ValueError(f"is:{value} is not an option; use one of {', '.join(options)}")


def _near(body: str) -> str | None:
    words, _, distance = body.rpartition(",")
    if not distance.strip().isdigit():
        words, distance = body, ""
    terms = [_quote(w) for w in re.findall(r"[^\s\"]+", words.replace('"', " "))]
    if len(terms) < 2:
        return None
    return f"NEAR({' '.join(terms)}{', ' + distance.strip() if distance else ''})"


def parse(query: str, filters: ItemFilters, match: MatchField | None = None) -> ParsedQuery:
    """Raises ValueError, worded for the person who typed the query, on an operator it cannot
    honour or on exclusions with nothing to exclude them from."""
    changes: dict[str, Any] = {"match": match}
    excluded_kinds = list(filters.exclude_kinds)
    terms: list[str] = []  # FTS terms, with "OR" markers between alternatives
    excluded: list[str] = []
    words: list[str] = []
    for token in _TOKEN.findall(query):
        negated = token.startswith("-") and len(token) > 1
        body = token[1:] if negated else token
        if (operator := _OPERATOR.fullmatch(body)) and operator[1].lower() in _OPERATORS:
            _operator(operator[1].lower(), operator[2].strip('"'), negated, changes, excluded_kinds)
            continue
        if body == "OR" and not negated:
            terms.append("OR")
            continue
        if near := _NEAR.fullmatch(body):
            if term := _near(near[1]):
                (excluded if negated else terms).append(term)
            continue
        phrase = body.startswith('"')
        text = body.strip('"') if phrase else body.rstrip("*")
        if not re.search(r"\w", text):
            continue
        term = _quote(text) + ("*" if not phrase and body.endswith("*") else "")
        (excluded if negated else terms).append(term)
        if not negated:
            words.append(text)

    # An OR needs a term on each side
    joined: list[str] = []
    for term in terms:
        if term == "OR" and (not joined or joined[-1] == "OR"):
            continue
        joined.append(term)
    if joined and joined[-1] == "OR":
        joined.pop()

    if excluded and not joined:
        raise ValueError("an exclusion needs at least one word to exclude it from")
    expression = None
    if joined:
        expression = " ".join(joined)
        if excluded:
            expression = f"({expression}) NOT ({' OR '.join(excluded)})"
        if field := changes["match"]:
            expression = f"{{{_MATCH_COLUMNS[field]}}} : ({expression})"
    del changes["match"]
    return ParsedQuery(
        expression=expression,
        text=" ".join(words) or None,
        filters=replace(filters, **changes, exclude_kinds=tuple(dict.fromkeys(excluded_kinds))),
    )
