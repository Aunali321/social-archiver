"""Reading Twitter accounts off their tweets, a hundred to a TweetResultsByRestIds request."""

from social_archiver.core.profile_refresh import AuthorReading
from social_archiver.core.profile_store import UnobservedProfile
from social_archiver.core.profiles import ProfileSnapshot, ProfileStatus
from social_archiver.platforms.twitter.client import TwitterClient


class TweetAuthorReader:
    platform = "twitter"
    batch = 100

    def __init__(self, tw_client: TwitterClient):
        self.tw_client = tw_client

    async def read(self, item_ids: list[str]) -> AuthorReading:
        result = await self.tw_client.get_tweets_by_ids(item_ids, chunk_size=self.batch)
        return AuthorReading(
            [tweet["author_profile"] for tweet in result.tweets if tweet.get("author_profile")],
            result.unavailable,
        )

    def refused(self, account: UnobservedProfile, reason: str | None) -> ProfileSnapshot:
        match reason:
            case str() if "Suspended" in reason:
                return ProfileSnapshot(account.profile_id, account.handle, status=ProfileStatus.SUSPENDED)
            case str() if "no longer exists" in reason:
                return ProfileSnapshot(account.profile_id, account.handle, status=ProfileStatus.DELETED)
            case str() if "Protected" in reason:
                return ProfileSnapshot(account.profile_id, account.handle, private=True)
            case _:
                return ProfileSnapshot(account.profile_id, account.handle)
