import argparse
from collections.abc import Sequence


def build_parser(
    description: str, categories: Sequence[str], source_kinds: Sequence[str] = ()
) -> argparse.ArgumentParser:
    """The shared archive/upload/embed/run command line every platform exposes.

    `source_kinds` is empty for a platform that cannot yet walk an account or a subreddit, and
    the subcommand is left out rather than offered and refused."""
    parser = argparse.ArgumentParser(description=description)
    commands = parser.add_subparsers(dest="command", required=True)

    archive = commands.add_parser("archive", help="Fetch new content and download media to disk")
    archive.add_argument("--history", action="store_true", help="Fetch the full history, not just new items")
    archive.add_argument("--category", choices=categories, help="Archive a single category")
    archive.add_argument(
        "--retry-failed", action="store_true", help="Also retry items whose media download previously failed"
    )

    upload = commands.add_parser("upload", help="Send archived items to their Telegram channels")
    upload.add_argument("--retry-failed", action="store_true", help="Also retry items whose upload previously failed")

    embed = commands.add_parser("embed", help="Generate VLM descriptions and search embeddings")
    embed.add_argument("--retry-failed", action="store_true", help="Also retry items whose embedding previously failed")
    embed.add_argument(
        "--retry-refused", action="store_true", help="Also retry items the VLM previously declined on safety/policy"
    )

    if source_kinds:
        source = commands.add_parser("source", help="Archive an account or subreddit in full")
        source.add_argument("target", help="The account or subreddit to archive")
        source.add_argument(
            "--kind", choices=source_kinds, default=source_kinds[0], help="Which kind of source the target is"
        )
        source.add_argument(
            "--full",
            action="store_true",
            help="Walk the whole history again, rather than stopping at the newest item already held",
        )
        source.add_argument(
            "--no-media",
            action="store_true",
            help="Record items and their media urls without downloading; a later run collects the files",
        )

    run = commands.add_parser("run", help="Run archive, upload, and embed once, in order")
    run.add_argument("--history", action="store_true", help="Fetch the full history, not just new items")

    return parser
