"""One-time Reddit OAuth to obtain a refresh token, so the archiver authenticates
without a stored password (and 2FA is handled by the browser login).

Only REDDIT_CLIENT_ID is required — create an "installed app" at
https://www.reddit.com/prefs/apps with redirect uri http://localhost:8080 and it
issues just a client_id, no secret. (A "script"/"web" app also works; set
REDDIT_CLIENT_SECRET too.)

    uv run python scripts/reddit_auth.py

Authorize in the browser, then paste the printed line into .env.
"""

import asyncio
import socket
import sys
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import asyncpraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_archiver.platforms.reddit import config

REDIRECT_HOST = "localhost"
REDIRECT_PORT = 8080
REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}"
SCOPES = ["identity", "history", "read"]  # me(), saved/upvoted/downvoted, read posts/comments


def _receive_code() -> str:
    """Serve the single OAuth redirect on localhost and return its `code`."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((REDIRECT_HOST, REDIRECT_PORT))
    server.listen(1)
    connection, _ = server.accept()
    request_line = connection.recv(4096).decode("utf-8", "replace").split("\r\n", 1)[0]
    params = parse_qs(urlparse(request_line.split(" ")[1]).query)

    error = params.get("error", [None])[0]
    body = f"Reddit returned an error: {error}" if error else "Authorized. Return to the terminal."
    connection.sendall(f"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n{body}".encode())
    connection.close()
    server.close()
    if error:
        raise SystemExit(body)
    return params["code"][0]


async def main() -> None:
    if not config.REDDIT_CLIENT_ID:
        raise SystemExit("Set REDDIT_CLIENT_ID in .env first")

    reddit = asyncpraw.Reddit(
        client_id=config.REDDIT_CLIENT_ID,
        client_secret=config.REDDIT_CLIENT_SECRET or None,
        redirect_uri=REDIRECT_URI,
        user_agent=config.REDDIT_USER_AGENT,
    )
    url = reddit.auth.url(scopes=SCOPES, state="social-archiver", duration="permanent")
    print(f"Opening browser to authorize (if it doesn't open, paste this URL):\n{url}\n")
    webbrowser.open(url)

    code = await asyncio.get_running_loop().run_in_executor(None, _receive_code)
    refresh_token = await reddit.auth.authorize(code)
    await reddit.close()

    print(f"\nAdd this line to .env (then remove REDDIT_USERNAME/PASSWORD):\n\nREDDIT_REFRESH_TOKEN={refresh_token}\n")


if __name__ == "__main__":
    asyncio.run(main())
