"""WhatsApp captioning is the shared thread engine (a chat-day is the thread);
the WhatsApp-specific choices (labels, on-disk-only media) live in WhatsAppPort."""

from social_archiver.core.embed import ThreadEmbedJob as EmbedJob

__all__ = ["EmbedJob"]
