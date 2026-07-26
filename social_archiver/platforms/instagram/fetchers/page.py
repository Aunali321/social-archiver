from dataclasses import dataclass

from social_archiver.platforms.instagram.simple_media import SimpleMedia

FEED_PAGE_SIZE = 50  # a feed returns 21 unasked and rejects 100; 50 is the most it will serve


@dataclass(frozen=True, slots=True)
class MediaPage:
    """One API page of a paginated feed. `raw_count` is what the server returned
    before extraction, which is what separates 'end of the feed' from 'every item
    on this page failed to extract' — the latter must not stop the walk."""

    media: list[SimpleMedia]
    next_max_id: str
    raw_count: int
    resume_from: str | None = None

    @property
    def has_more(self) -> bool:
        return bool(self.next_max_id) and self.raw_count > 0

    @property
    def cursor(self) -> str:
        """What to store to resume the walk after this page. One feed resumes from its
        own max_id; a walk spanning several feeds has to say which one it is in."""
        return self.next_max_id if self.resume_from is None else self.resume_from
