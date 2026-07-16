import argparse
from collections.abc import Sequence


def build_parser(description: str, categories: Sequence[str]) -> argparse.ArgumentParser:
    """The shared archive/upload/embed/run/daemon command line every platform exposes."""
    parser = argparse.ArgumentParser(description=description)
    commands = parser.add_subparsers(dest="command", required=True)

    archive = commands.add_parser("archive", help="Fetch new content and download media to disk")
    archive.add_argument("--history", action="store_true", help="Fetch the full history, not just new items")
    archive.add_argument("--category", choices=categories, help="Archive a single category")

    upload = commands.add_parser("upload", help="Send archived items to their Telegram channels")
    upload.add_argument("--retry-failed", action="store_true", help="Also retry items whose upload previously failed")

    embed = commands.add_parser("embed", help="Generate VLM descriptions and search embeddings")
    embed.add_argument("--retry-failed", action="store_true", help="Also retry items whose embedding previously failed")

    run = commands.add_parser("run", help="Run archive, upload, and embed once, in order")
    run.add_argument("--history", action="store_true", help="Fetch the full history, not just new items")

    commands.add_parser("daemon", help="Run all jobs now, then again on an interval")

    return parser
