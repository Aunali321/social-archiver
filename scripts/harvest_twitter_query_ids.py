"""Harvest Twitter GraphQL query IDs from x.com's public JS bundles.

Query IDs rotate every few weeks. When TweetDetail/Search/Likes start returning
404s, run this (zero API cost — public CDN only) and update QUERY_IDS /
FALLBACK_QUERY_IDS in social_archiver/platforms/twitter/client.py.

    uv run python scripts/harvest_twitter_query_ids.py
"""

import asyncio
import json
import re

import httpx

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

TARGETS = {
    "TweetResultByRestId",
    "TweetResultsByRestIds",
    "TweetDetail",
    "Likes",
    "Bookmarks",
    "SearchTimeline",
    "UserTweets",
    "UserTweetsAndReplies",
    "BookmarkFolderTimeline",
    "UserByScreenName",
    "UserByRestId",
}

BUNDLE_RE = re.compile(r"https://abs\.twimg\.com/responsive-web/client-web(?:-legacy)?/[A-Za-z0-9.-]+\.js")
OP_RES = [
    re.compile(r'queryId\s*:\s*["\']([^"\']+)["\']\s*,\s*operationName\s*:\s*["\']([^"\']+)["\']'),
    re.compile(r'operationName\s*:\s*["\']([^"\']+)["\']\s*,\s*queryId\s*:\s*["\']([^"\']+)["\']'),
]

DISCOVERY_PAGES = [
    "https://x.com/?lang=en",
    "https://x.com/explore",
]


async def main():
    found: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"user-agent": UA}) as client:
        bundles: list[str] = []
        for page in DISCOVERY_PAGES:
            try:
                html = (await client.get(page)).text
            except httpx.HTTPError:
                continue
            bundles.extend(u for u in BUNDLE_RE.findall(html) if u not in bundles)

        print(f"scanning {len(bundles)} bundles")
        for url in bundles:
            text = (await client.get(url)).text
            for rx in OP_RES:
                for m in rx.finditer(text):
                    a, b = m.group(1), m.group(2)
                    op, qid = (b, a) if rx is OP_RES[0] else (a, b)
                    if op in TARGETS:
                        found.setdefault(op, qid)

    print(json.dumps(found, indent=2, sort_keys=True))
    missing = TARGETS - set(found)
    if missing:
        print(f"not found (may live in lazy chunks, or try while logged in): {sorted(missing)}")


if __name__ == "__main__":
    asyncio.run(main())
