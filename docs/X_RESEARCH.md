# X/Twitter Fetch Optimization Research

Goal: archive maximum liked/bookmarked content with minimum HTTP requests. User will
run the full optimized pipeline later; this session designs, implements, and validates
the methods. Every step logged here so work can resume mid-way.

Session start: 2026-07-13 ~02:40 (overnight autonomous run, @XCossale creds, careful pacing)

## Problem statement (measured 2026-07-13)

Current expander cost per 5 likes: ~24 HTTP requests. Breakdown of waste:
- Viral quoted tweet (marklevinshow, 4717 replies): 11 TweetDetail pages fetched, 391
  tweets scanned, exactly 1 archived. Pages 2-11 pure waste (stranger replies).
- josh_hammer conversation: 7 pages, same pattern.
- Liked-reply scanning of conversations is 100% redundant: every like is already a seed
  (and old likes aren't in the seed set on incremental runs, so scanning can't match them).
- Parents/ancestors always appear on TweetDetail page 1 (rendered above focal tweet).
- Standalone tweets (reply_count==0, no parent) get a conversation fetch that returns
  nothing new — the timeline payload already had all their data.
- API-call counter counts get_thread() invocations, not HTTP requests → logs undercount.

Only legitimate reason to paginate past page 1: continuation of the author's own
self-reply thread when it's too long for page 1.

## Optimization candidates (to research/verify)

1. **Relevance-stop pagination**: after page 1, keep paginating only while pages add
   tweets by authors in the focal tweet's ancestor/self-thread chain.
2. **Skip conversation fetch entirely** for standalones: reply_count==0 AND not a reply
   AND (if quote/RT: refs resolvable from embedded payload).
3. **Batch tweet lookup** (`TweetResultsByRestIds`?): resolve quoted/linked/retweeted/
   parent IDs ~100 per request instead of 1 TweetDetail each. Walk quote chains and
   parent chains breadth-first: 1 request per depth level for ALL pending IDs.
4. **Search timeline** as targeted self-thread fetch: `from:author conversation_id:X`
   returns only the author's thread tweets, no viral reply noise. Fallback/verification.
5. Liked replies: user suggestion — they come from the Likes timeline (profile) already;
   conversation scanning for them is dead code to remove.

## Step log

### 02:40 — located references
`reference/bird` (TS Twitter client the current client was ported from) and
`reference/birdclaw` exist. Searching them for batch endpoints + query IDs next.

### 02:50 — reference review done
- `bird`: same endpoints we already ported (TweetDetail, Likes, Bookmarks, SearchTimeline).
  No batch lookup. BUT `scripts/update-query-ids.ts` proves query IDs for ANY GraphQL op
  can be harvested from Twitter's public JS bundles (abs.twimg.com/responsive-web/client-web/*.js).
  Its thread-filters.ts confirms our author-chain logic matches bird's.
- `birdclaw`: archive app built ON bird — no new endpoints. Notable: it supports importing
  the OFFICIAL Twitter data export (archive-import.ts). That's the zero-request path for
  full LIKE history (like.js in the export) — worth telling user as an option for initial
  backfill (bookmarks are NOT in official exports).
- Fresher query IDs than ours in bird/src/lib/query-ids.json:
  TweetDetail _NvJCnIjOW__EP5-RF197A, Likes ETJflBunfqNa1uE1mBPCaw, SearchTimeline 6AAys3t42mosm_yTI_QENg.
  Ours still work (verified earlier tonight), keeping ours as primary, adding bird's as fallbacks.

### Next: harvest query IDs from x.com JS bundles
Target ops: TweetResultByRestId, TweetResultsByRestIds (batch!), TweetDetail, Likes,
Bookmarks, SearchTimeline, UserTweets. Method: fetch x.com HTML -> bundle URLs -> regex
operationName/queryId pairs; if not in main bundle, locate the `api` chunk via the chunk
manifest. This is public CDN traffic, no API rate-limit cost.

### 03:00 — bundle harvest + batch endpoint VERIFIED LIVE
Harvested from x.com public bundles (scratchpad/harvest_query_ids.py):
```
TweetResultByRestId    -4_LMahNlI4MuLJ-EAFEog
TweetResultsByRestIds  7nfIZg-03g-BuVG0Oa1fXA   <- BATCH lookup
TweetDetail            jd3V43oDY9cY7obs1YMfbQ   (fresher than ours)
Likes                  tl9f_I0xyREhFd5KMzuO7w
SearchTimeline         Bcw3RzK-PatNAmbnw54hFw
UserTweets             hr4gzZONlq23okjU8fIe_A
UserTweetsAndReplies   FIFgycIi-CNJcV0R-135Uw
```
(Bookmarks op not in logged-out bundles; current ID RV1g3b8n_SGOHwkqKYSCFw still works.)

Live test of TweetResultsByRestIds (1 request, 5 IDs incl. 1 bogus):
- HTTP 200 with our existing TWEET_DETAIL_FEATURES, no extra features needed.
- Full data per tweet incl. its OWN quoted_tweet_id -> the whole ripplebrain->marklevinshow
  ->josh_hammer->tperkins chain resolved in ONE request (old cost: 19 pages across 3
  TweetDetail fetches).
- Bogus/deleted ID -> empty item `{}` (no reason text). Missing = input_ids - returned_ids.
- reply_count present -> can decide if a conversation fetch is needed at all.

### New expansion algorithm (design)
Phase 0: Likes/Bookmarks timeline pages (unavoidable, 20/page, early-stop on known IDs).
Phase 1: BREADTH-FIRST BATCH CLOSURE — collect unresolved refs from all known tweets
  (quoted_tweet_id, linked_tweet_ids, retweeted_tweet_id, in_reply_to chain), batch-fetch
  up to ~100/request, repeat until no new refs. Cost ~= graph depth, not tweet count.
  Missing IDs -> tombstones.
Phase 2: THREAD DISCOVERY — TweetDetail ONLY for conversations where reply_count > 0
  (self-replies possible; reply_count==0 => provably standalone, 0 requests), one per
  unique conversation, page 1 + relevance-stop pagination (continue only while pages add
  tweets by focal/ancestor-chain authors).
Also: remove `_check_seed_replies` (dead code — every like is already a seed; liked
replies come from the profile Likes timeline, per user's suggestion).

OPEN QUESTION to test: for very long self-threads, does Bottom-cursor pagination return
the thread continuation, or is there an in-module "ShowMore" item cursor we must follow?
Test on a real long thread before trusting relevance-stop.

### 03:20 — TweetDetail structure probed (3 real conversations dumped)
- Ancestors render as top-level `tweet-<id>` entries above focal. Complete for normal depths.
- Reply branches = `conversationthread-<id>` modules; the AUTHOR SELF-THREAD is the first
  module (all author tweets consecutive). In-module `ShowMore` cursors appear on branches.
- **BUG FOUND in our parser**: `tweetdetailrelatedtweets-*` modules (recommended, UNRELATED
  tweets) are ingested into the conversation cache by `_parse_tweets_from_instructions`.
  Must filter by entryId prefix.

### 03:30 — SearchTimeline validated (qid Bcw3RzK-PatNAmbnw54hFw, bird-style POST)
- `from:AUTHOR conversation_id:CONV` returns ALL the author's tweets in a conversation:
  root + numbered thread + branch replies. Tested on visakanv 6-tweet thread: complete.
  Even surfaced an author reply hidden behind TweetDetail's in-module ShowMore cursor.
- **OR-batching works**: `(from:A conversation_id:X) OR (from:B conversation_id:Y) ...`
  8 groups / 574 chars accepted. Page cap = 20 tweets regardless of count param; Bottom
  cursor paginates. `filter:self_threads` operator also works.
- Caveats: search can't see protected accounts; page-1 absence != nonexistence (paginate
  to exhaustion); no tombstone reasons.

## FINAL DESIGN — expander v2 (phased, verified building blocks)

Phase 0  seeds: Likes/Bookmarks timelines (unchanged, known-ids early stop).
Phase 1  BATCH CLOSURE: collect unresolved refs (quoted_tweet_id, linked_tweet_ids,
         retweeted_tweet_id, in_reply_to chains) from all known tweets; fetch via
         TweetResultsByRestIds (chunk ~=100, test max); returned tweets add new refs;
         repeat to closure. Missing IDs -> tombstones (no reason text available).
         Cost ~= graph depth (1-3 requests), covers ALL parent+quote+link chains at once.
Phase 2  THREAD DISCOVERY: for each tweet in set with reply_count>0, pair
         (author, conversation_id); dedup; OR-batch ~5 pairs/query into SearchTimeline;
         paginate Bottom until exhausted. Keep chain-connected author tweets (same
         connectivity logic as today, benefit-of-the-doubt when parent tombstoned).
         New thread tweets loop back into Phase 1 for their refs.
         reply_count==0 -> provably standalone -> ZERO requests (huge win: no more
         1-11 TweetDetail pages per conversation).
Fallback TweetDetail page 1 (+ relevance-stop pagination) when: author is protected
         (search blind) or search returns nothing for a pair where we know >=1 tweet
         exists (index gap detection is free: we always know at least the seed).
Also:    honest HTTP request counter on TwitterClient; filter tweetdetailrelatedtweets
         from TweetDetail parsing; remove redundant _check_seed_replies (likes come from
         the profile Likes timeline as user suggested).

Projected cost, 100 new likes / ~60 convs / 15 quotes: ~5 timeline + ~3 batch + ~10-14
search = **~20 requests** vs ~100-250 today (10x). Rate-limit safe: load split across
three separate buckets (timeline, batch lookup, search).

Rate-limit notes: SearchTimeline historically ~50 req/15min — pace history mode;
incremental daemon runs use a handful. TweetResultsByRestIds bucket generous (web uses
it constantly). Keep 1.5s page_delay everywhere.

### Remaining to verify in test matrix
- [ ] TweetResultsByRestIds max batch size (test 100)
- [ ] Bookmarks endpoint works (current qid) + expander on bookmarks
- [ ] Search cursor pagination loop (multi-page OR batch)
- [ ] End-to-end v2 expander vs v1 request counts on same 5 likes
- [ ] Protected-author fallback path (if a protected author appears)

### 03:45 — v2 IMPLEMENTED AND VERIFIED LIVE
Code: client.py (get_tweets_by_ids, search_tweets, get_conversation_author_tweets,
request counter, 429 wait-and-retry on x-rate-limit-reset, relevance-stop get_thread,
related-tweets parse filter, author_protected + shallow flags, fresh query IDs with
fallbacks), expander.py (full phased rewrite).

Measured results (all live, @XCossale):
| Case | v1 cost | v2 cost | Result |
|------|---------|---------|--------|
| 5 fresh likes, 2 quotes, 2 threads | ~24 req | 6 req | 12 tweets, origins correct |
| 4-level viral quote chain (4717-reply conv) | ~21 req | 10 req | chain complete |
| 6-tweet self-thread + embedded quote | ~8 req | 6 req | complete, @-replies excluded |
| 3 bookmarks + context | untested in v1 | 9 req | works, bookmarked origin |
| 100-id batch lookup | n/a | 1 req | 70 found / 30 missing handled |

Deep quote chains cost 1 batch + 1 search per LEVEL (inherent: level N+1 unknown until
N resolved). Flat workloads amortize: N likes' quotes resolve in ~1 batch total.

Rate-limit behavior: separate buckets for timeline / TweetResultsByRestIds / search /
TweetDetail-fallback spread the load; on 429 the client sleeps until x-rate-limit-reset
(capped 16 min) and retries once, so history mode survives bucket exhaustion instead of
crashing. ~50 requests spent tonight across all testing, no 429 encountered.

### Operational notes for the full run (user: read this)
- Incremental daemon run: a handful of requests per cycle. Non-issue.
- `--history` full backfill: cost ≈ likes_pages + convs_with_replies/5 searches +
  a few batches. For ~3000 likes / ~2000 conversations expect ~550-700 requests,
  dominated by search (bucket ~50/15min) -> the 429-wait logic will stretch it over
  ~2-3h unattended. That's the safe, polite profile.
- Fastest zero-risk backfill for LIKE HISTORY: your official Twitter data export
  (settings -> download archive) contains like.js with every like ID ever; feed those
  IDs through get_tweets_by_ids at 100/request (~30 requests for 3000 likes!), then
  expansion on top. Bookmarks are NOT in the export. birdclaw has an archive importer
  for reference. Recommend: request the export now, it takes ~24h to arrive.
- Query IDs rotate every few weeks. scratchpad/harvest_query_ids.py re-harvests them
  from public CDN bundles (zero API cost). If TweetDetail/Search start 404ing, rerun it
  and update QUERY_IDS/FALLBACK_QUERY_IDS in client.py.

### What changed in the pipeline (files)
- social_archiver/platforms/twitter/client.py — new endpoints + honest counting + 429 handling
- social_archiver/platforms/twitter/expander.py — phased v2 (batch closure + OR-batched
  conversation search + TweetDetail fallback for protected/index-gap)
- Processor/scheduler/DB: unchanged (same expand() API). Tombstones/origins/thread
  metadata behave as before, now with is_tombstone + linked origins from earlier tonight.

### Not run tonight (needs you)
- Full pipeline incl. Telegram upload (would post to your channels).
- Protected-author fallback path (no protected author appeared in test data; code path
  exists, exercised only the search-gap variant logically).

### 03:55 — final scale test + session end
30 likes -> 56 tweets (16 quoted, 10 thread), **21 HTTP requests** including auth +
2 timeline pages. v1 equivalent: ~80-150. Session total ≈ 105 requests spread over
~1.5h across four separate rate buckets; zero 429s.

DONE. Pipeline ready for your run: `--once` (incremental) or `--history` (backfill).
Read "Operational notes" above before the backfill; strongly consider the data-export
path for like history.
