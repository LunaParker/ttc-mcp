"""Spec 6.9: the in-process Google OAuth gate. Authentication is GoogleProvider (dynamic client registration,
consent remembered); authorization is the email allowlist, checked on every request."""
from __future__ import annotations

from typing import Any

from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext

from ttc_mcp.config import Gate

SCOPES = ["openid", "email"]
PREFIX = "ttc"


class EmailAllowlist(Middleware):
    """Only a verified, listed email passes; anything else gets a JSON-RPC authorization error rather than a 500."""

    def __init__(self, emails: frozenset[str]) -> None:
        self.emails = emails

    async def on_request(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        token = get_access_token()
        claims = token.claims if token is not None else {}
        email = str(claims.get("email") or "").strip().lower()
        verified = claims.get("email_verified")
        if not email or email not in self.emails or not (verified is True or str(verified).lower() == "true"):
            raise AuthorizationError(f"This server is restricted; {email or 'your account'} is not authorized.")
        return await call_next(context)


def client_storage(gate: Gate):
    """OAuth state: Firestore when a project is named, sanitized for Firestore ids, prefixed 'ttc' (Claude Code's CIMD client id is
    one URL shared by every gateway) and Fernet-encrypted; None means FastMCP's in-memory default."""
    if not gate.firestore_project:
        return None
    from cryptography.fernet import Fernet
    from key_value.aio.stores.firestore import FirestoreStore, FirestoreV1CollectionSanitizationStrategy, FirestoreV1KeySanitizationStrategy
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
    from key_value.aio.wrappers.prefix_collections import PrefixCollectionsWrapper

    store = FirestoreStore(project=gate.firestore_project, key_sanitization_strategy=FirestoreV1KeySanitizationStrategy(),
                           collection_sanitization_strategy=FirestoreV1CollectionSanitizationStrategy())
    return FernetEncryptionWrapper(PrefixCollectionsWrapper(store, prefix=PREFIX), fernet=Fernet(gate.storage_key))


def build_auth(gate: Gate):
    from fastmcp.server.auth.providers.google import GoogleProvider

    return GoogleProvider(client_id=gate.client_id, client_secret=gate.client_secret, base_url=gate.base_url, required_scopes=SCOPES,
                          jwt_signing_key=gate.jwt_signing_key, client_storage=client_storage(gate), require_authorization_consent="remember")
