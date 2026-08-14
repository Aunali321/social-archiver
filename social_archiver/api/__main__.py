import os

import uvicorn

from social_archiver.core import config
from social_archiver.core.utils import setup_logging


def main():
    setup_logging(config.LOGS_DIR / "web.log")
    uvicorn.run(
        "social_archiver.api:app",
        host=os.getenv("WEB_HOST", "0.0.0.0"),
        port=int(os.getenv("WEB_PORT", "8080")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
