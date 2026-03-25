import logging
from typing import List
from twitter_archiver.twitter_client import TwitterClient
from twitter_archiver.simple_tweet import SimpleTweet

logger = logging.getLogger(__name__)


class LikesFetcher:
    def __init__(self, tw_client: TwitterClient):
        self.tw_client = tw_client

    async def fetch_likes(self, amount: int = 0) -> List[SimpleTweet]:
        logger.info(f"Fetching likes (amount={amount if amount > 0 else 'all'})")

        result = await self.tw_client.get_all_likes(
            limit=amount,
            page_delay=1.5,
        )

        if not result.get("success"):
            error = result.get("error", "Unknown error")
            logger.error(f"Failed to fetch likes: {error}")
            return []

        raw_tweets = result.get("tweets", [])
        tweets = []
        for raw in raw_tweets:
            try:
                tweets.append(SimpleTweet.from_api_dict(raw))
            except Exception as e:
                logger.warning(f"Failed to parse tweet {raw.get('id', 'unknown')}: {e}")
                continue

        logger.info(f"Fetched {len(tweets)} likes")
        return tweets
