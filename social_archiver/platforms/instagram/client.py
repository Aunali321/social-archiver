import logging

from instagrapi import Client
from instagrapi.exceptions import ChallengeRequired

from social_archiver.platforms.instagram import config

logger = logging.getLogger(__name__)


class InstagramClient:
    def __init__(self):
        self.client = Client()
        self.client.delay_range = config.INSTAGRAM_DELAY_RANGE
        self._authenticated = False
        self.request_count = 0  # every private API request, for honest cost logging

    def private_request(self, endpoint: str, params: dict | None = None) -> dict:
        self.request_count += 1
        return self.client.private_request(endpoint, params=params)

    def login(self):
        if config.SESSION_PATH.exists():
            try:
                logger.info("Loading existing Instagram session")
                self.client.load_settings(config.SESSION_PATH)
                self.client.login(config.INSTAGRAM_USERNAME, config.INSTAGRAM_PASSWORD)
                self._authenticated = True
                logger.info("Successfully loaded session")
                return
            except Exception as e:
                logger.warning(f"Failed to load session: {e}, trying sessionid login")

        if config.INSTAGRAM_SESSIONID:
            logger.info("Logging in with sessionid")
            try:
                self.client.login_by_sessionid(config.INSTAGRAM_SESSIONID)
                self.client.dump_settings(config.SESSION_PATH)
                self._authenticated = True
                logger.info("Successfully logged in with sessionid and saved session")
                return
            except Exception as e:
                logger.warning(f"Failed to login with sessionid: {e}, trying username/password")

        logger.info("Logging into Instagram with username/password")
        try:
            self.client.login(config.INSTAGRAM_USERNAME, config.INSTAGRAM_PASSWORD)
            self.client.dump_settings(config.SESSION_PATH)
            self._authenticated = True
            logger.info("Successfully logged in and saved session")
        except ChallengeRequired:
            logger.info("2FA challenge required, waiting for code input...")
            code = input("Enter 2FA code: ").strip()
            self.client.login(config.INSTAGRAM_USERNAME, config.INSTAGRAM_PASSWORD, verification_code=code)
            self.client.dump_settings(config.SESSION_PATH)
            self._authenticated = True
            logger.info("Successfully logged in with 2FA and saved session")

    def is_authenticated(self) -> bool:
        return self._authenticated

    def get_user_id_by_username(self, username: str) -> str:
        return self.client.user_info_by_username(username).pk
