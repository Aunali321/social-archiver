import os

import uvicorn

from social_archiver.core.utils import setup_logging
from social_archiver.core import config

if __name__ == "__main__":
    setup_logging(config.LOGS_DIR / "web.log")
    uvicorn.run(
        "social_archiver.web:app",
        host=os.getenv("WEB_HOST", "0.0.0.0"),
        port=int(os.getenv("WEB_PORT", "8080")),
        log_config=None,
    )
