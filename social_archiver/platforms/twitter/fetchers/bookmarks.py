import logging

from social_archiver.platforms.twitter.client import TwitterClient
from social_archiver.platforms.twitter.simple_tweet import SimpleTweet

logger = logging.getLogger(__name__)


class BookmarksFetcher:
    def __init__(self, tw_client: TwitterClient):
        self.tw_client = tw_client

    async def fetch_bookmarks(self, amount: int = 0) -> list[SimpleTweet]:
        logger.info(f"Fetching bookmarks (amount={amount if amount > 0 else 'all'})")

        result = await self.tw_client.get_all_bookmarks(limit=amount, page_delay=1.5)
        if not result.get("success"):
            logger.error(f"Failed to fetch bookmarks: {result.get('error', 'Unknown error')}")
            return []

        tweets = []
        for raw in result.get("tweets", []):
            try:
                tweets.append(SimpleTweet.from_api_dict(raw))
            except Exception as e:
                logger.warning(f"Failed to parse tweet {raw.get('id', 'unknown')}: {e}")

        logger.info(f"Fetched {len(tweets)} bookmarks")
        return tweets
