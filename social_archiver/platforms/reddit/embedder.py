"""Reddit captioning is the shared thread engine; the Reddit-specific choices
(labels, media restore) live in RedditPort."""

from social_archiver.core.embed import ThreadEmbedJob as EmbedJob

__all__ = ["EmbedJob"]
