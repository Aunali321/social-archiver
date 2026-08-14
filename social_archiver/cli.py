"""One console entry for everything: platform jobs, the server, the daemon, search, MCP.

`social-archiver reddit archive` is `python -m social_archiver.platforms.reddit archive` with
the argv shifted, so each platform's own parser stays the single definition of its flags.
"""

import argparse
import asyncio
import importlib
import logging
import sys
import textwrap

from social_archiver.core.config import PLATFORMS, ConfigError

logger = logging.getLogger(__name__)

_COMMANDS = ("serve", "daemon", "search", "stats", "mcp", *PLATFORMS)


def _delegate(module: str, prog: str, argv: list[str]):
    sys.argv = [prog, *argv]
    importlib.import_module(module).main()


def main():
    command, *rest = sys.argv[1:] or ["--help"]
    try:
        match command:
            case platform if platform in PLATFORMS:
                _delegate(f"social_archiver.platforms.{platform}.__main__", f"social-archiver {platform}", rest)
            case "serve":
                _delegate("social_archiver.api.__main__", "social-archiver serve", rest)
            case "daemon":
                _delegate("social_archiver.daemon", "social-archiver daemon", rest)
            case "mcp":
                _delegate("social_archiver.mcp_server", "social-archiver mcp", rest)
            case "search":
                _search(rest)
            case "stats":
                asyncio.run(_stats())
            case _:
                print(
                    textwrap.dedent(f"""\
                    usage: social-archiver <command> ...

                      serve                     web UI, API, scheduler and workers
                      daemon                    scheduler and workers, no HTTP
                      search <query>            search the archive (--semantic for vector search)
                      stats                     per-platform archive totals
                      mcp                       MCP server on stdio
                      {" | ".join(PLATFORMS)}
                                                a platform's own jobs; see `social-archiver <platform> -h`""")
                )
                sys.exit(0 if command in ("--help", "-h") else 2)
    except KeyboardInterrupt:
        logger.info("Shutting down")
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


def _search(argv: list[str]):
    parser = argparse.ArgumentParser(prog="social-archiver search", description="Search the archive")
    parser.add_argument("query")
    parser.add_argument("--platform", action="append", choices=PLATFORMS, help="repeatable; defaults to all")
    parser.add_argument("--category")
    parser.add_argument("--author")
    parser.add_argument("--semantic", action="store_true", help="vector search instead of full-text")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    asyncio.run(_run_search(args))


async def _run_search(args):
    from social_archiver.core import config
    from social_archiver.read import ArchiveReader, ItemFilters
    from social_archiver.read import semantic as semantic_search

    reader = ArchiveReader(config.DATA_DIR)
    platforms = tuple(dict.fromkeys(args.platform)) if args.platform else ()
    try:
        if args.semantic:
            if not semantic_search.available():
                sys.exit("semantic search is not configured; enable EMBEDDING_ENABLED and run embed jobs")
            for hit in await semantic_search.search(args.query, platforms, limit=args.limit):
                item = await reader.get(hit.platform, hit.item_id)
                _print_hit(item, hit.caption, hit.score) if item else None
        else:
            filters = ItemFilters(platforms=platforms, category=args.category, author=args.author)
            for hit in await reader.search(args.query, filters, limit=args.limit):
                _print_hit(hit.item, hit.snippet, None)
    finally:
        await reader.close()


def _print_hit(item, snippet: str | None, score: float | None):
    when = item.created_at.strftime("%Y-%m-%d") if item.created_at else "????-??-??"
    body = " ".join((snippet or item.text or "").split())[:160]
    tag = f" [{score:.3f}]" if score is not None else ""
    print(f"{item.platform:9} {when} @{item.author_username}{tag}\n  {body}\n  {item.post_url}\n")


async def _stats():
    from social_archiver.core import config
    from social_archiver.read import ArchiveReader

    reader = ArchiveReader(config.DATA_DIR)
    try:
        for platform in await reader.present():
            stats = await reader.stats(platform)
            span = f"{stats.oldest:%Y-%m} .. {stats.newest:%Y-%m}" if stats.oldest else "empty"
            categories = ", ".join(f"{name} {count:,}" for name, count in stats.categories.items())
            print(f"{platform:9} {stats.total:>9,} items  {span}  ({categories})")
    finally:
        await reader.close()


if __name__ == "__main__":
    main()
