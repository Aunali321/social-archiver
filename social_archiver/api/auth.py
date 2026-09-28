"""Sign-in for the web UI and OAuth for the MCP endpoint, both backed by core.auth.

The MCP SDK serves the OAuth endpoints (/register, /authorize, /token, metadata) and calls
into ArchiveOAuth. Its /authorize hands the browser to the viewer's /login page, where the
owner signs in if needed and approves the client; approval mints the authorization code and
sends the browser back to the client.
"""

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import BaseModel

from social_archiver.core import config
from social_archiver.core.auth import AuthStore, TokenKind

SESSION_COOKIE = "archive_session"
SESSION_TTL = 30 * 86_400
ACCESS_TTL = 3_600
REFRESH_TTL = 90 * 86_400
CODE_TTL = 300
# The one resource owner every token acts for
OWNER = "owner"

store = AuthStore(config.DATA_DIR / "auth.db")


@dataclass(slots=True)
class _Pending:
    client: OAuthClientInformationFull
    params: AuthorizationParams
    expires_at: float


class ArchiveOAuth(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """Pending approvals and codes live only minutes, so they stay in memory; a restart in
    the middle of a sign-in costs the client a retry. Clients and tokens persist."""

    def __init__(self, store: AuthStore):
        self.store = store
        self._pending: dict[str, _Pending] = {}
        self._codes: dict[str, AuthorizationCode] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return await self.store.client(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull):
        await self.store.register_client(client_info)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        now = time.time()
        self._pending = {key: p for key, p in self._pending.items() if p.expires_at > now}
        request_id = secrets.token_urlsafe(24)
        self._pending[request_id] = _Pending(client, params, now + CODE_TTL)
        return f"{config.PUBLIC_URL}/login?authorize={request_id}"

    def pending(self, request_id: str) -> _Pending:
        pending = self._pending.get(request_id)
        if pending is None or pending.expires_at < time.time():
            raise HTTPException(404, "this sign-in request has expired; start again from the app")
        return pending

    def decide(self, request_id: str, approve: bool) -> str:
        """Where the browser goes next: back to the client with a code, or with a refusal."""
        pending = self.pending(request_id)
        del self._pending[request_id]
        redirect_uri = str(pending.params.redirect_uri)
        if not approve:
            return construct_redirect_uri(redirect_uri, error="access_denied", state=pending.params.state)
        now = time.time()
        self._codes = {key: c for key, c in self._codes.items() if c.expires_at > now}
        code = AuthorizationCode(
            code=secrets.token_urlsafe(32),
            scopes=pending.params.scopes or [],
            expires_at=now + CODE_TTL,
            client_id=pending.client.client_id,
            code_challenge=pending.params.code_challenge,
            redirect_uri=pending.params.redirect_uri,
            redirect_uri_provided_explicitly=pending.params.redirect_uri_provided_explicitly,
            resource=pending.params.resource,
            subject=OWNER,
        )
        self._codes[code.code] = code
        return construct_redirect_uri(redirect_uri, code=code.code, state=pending.params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._codes.get(authorization_code)
        return code if code and code.client_id == client.client_id and code.expires_at > time.time() else None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self._codes.pop(authorization_code.code, None)
        return await self._grant(client.client_id, authorization_code.scopes, secrets.token_urlsafe(16))

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        stored = await self.store.lookup(refresh_token, TokenKind.REFRESH)
        if stored is None or stored.client_id != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=stored.scopes,
            expires_at=stored.expires_at,
            subject=OWNER,
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        """Rotates both tokens: the old pair stops working the moment the new one exists."""
        stored = await self.store.lookup(refresh_token.token, TokenKind.REFRESH)
        if stored is None:
            raise TokenError("invalid_grant", "refresh token revoked")
        await self.store.revoke_grant(stored.grant_id)
        return await self._grant(client.client_id, scopes or refresh_token.scopes, stored.grant_id)

    async def load_access_token(self, token: str) -> AccessToken | None:
        stored = await self.store.lookup(token, TokenKind.ACCESS)
        if stored is None:
            return None
        return AccessToken(
            token=token,
            client_id=stored.client_id,
            scopes=stored.scopes,
            expires_at=stored.expires_at,
            subject=OWNER,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken):
        stored = await self.store.lookup(token.token, TokenKind.ACCESS) or await self.store.lookup(
            token.token, TokenKind.REFRESH
        )
        if stored is not None:
            await self.store.revoke_grant(stored.grant_id)

    async def _grant(self, client_id: str, scopes: list[str], grant_id: str) -> OAuthToken:
        access, _ = await self.store.issue(TokenKind.ACCESS, ACCESS_TTL, client_id, scopes, grant_id)
        refresh, _ = await self.store.issue(TokenKind.REFRESH, REFRESH_TTL, client_id, scopes, grant_id)
        return OAuthToken(
            access_token=access,
            expires_in=ACCESS_TTL,
            scope=" ".join(scopes) or None,
            refresh_token=refresh,
        )


provider = ArchiveOAuth(store)


def mcp_auth() -> AuthSettings:
    return AuthSettings(
        issuer_url=config.PUBLIC_URL,
        resource_server_url=f"{config.PUBLIC_URL}/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
    )


async def require_session(archive_session: Annotated[str | None, Cookie()] = None):
    """Dependency guarding every /api router except this one."""
    if not archive_session or await store.lookup(archive_session, TokenKind.SESSION) is None:
        raise HTTPException(401, "sign in required")


router = APIRouter(prefix="/api/auth")

# Serialises sign-in attempts and holds each failure for a second, which caps password
# guessing at one try per second however many connections an attacker opens
_attempts = asyncio.Lock()


class LoginIn(BaseModel):
    password: str


class SessionOut(BaseModel):
    authenticated: bool


class AuthorizationOut(BaseModel):
    client_name: str
    redirect_uri: str


class DecisionIn(BaseModel):
    approve: bool


class RedirectOut(BaseModel):
    redirect: str


@router.post("/login", response_model=SessionOut)
async def login(body: LoginIn, response: Response) -> SessionOut:
    async with _attempts:
        if not secrets.compare_digest(body.password.encode(), config.WEB_PASSWORD.encode()):
            await asyncio.sleep(1)
            raise HTTPException(401, "wrong password")
    token, _ = await store.issue(TokenKind.SESSION, SESSION_TTL)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL,
        httponly=True,
        secure=config.PUBLIC_URL.startswith("https://"),
        samesite="lax",
    )
    return SessionOut(authenticated=True)


@router.post("/logout", response_model=SessionOut)
async def logout(response: Response, archive_session: Annotated[str | None, Cookie()] = None) -> SessionOut:
    if archive_session:
        await store.revoke(archive_session)
    response.delete_cookie(SESSION_COOKIE)
    return SessionOut(authenticated=False)


@router.get("/session", response_model=SessionOut)
async def session(archive_session: Annotated[str | None, Cookie()] = None) -> SessionOut:
    return SessionOut(
        authenticated=bool(archive_session) and await store.lookup(archive_session, TokenKind.SESSION) is not None
    )


@router.get("/authorize/{request_id}", response_model=AuthorizationOut, dependencies=[Depends(require_session)])
async def authorization(request_id: str) -> AuthorizationOut:
    pending = provider.pending(request_id)
    return AuthorizationOut(
        client_name=pending.client.client_name or pending.client.client_id,
        redirect_uri=str(pending.params.redirect_uri),
    )


@router.post("/authorize/{request_id}", response_model=RedirectOut, dependencies=[Depends(require_session)])
async def decide(request_id: str, body: DecisionIn) -> RedirectOut:
    return RedirectOut(redirect=provider.decide(request_id, body.approve))
