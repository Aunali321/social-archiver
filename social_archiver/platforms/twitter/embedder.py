"""Twitter captioning is the shared thread engine; the Twitter-specific choices
(labels, quoted-tweet context, media restore) live in TwitterPort."""

from social_archiver.core.embed import ThreadEmbedJob as EmbedJob

__all__ = ["EmbedJob"]
