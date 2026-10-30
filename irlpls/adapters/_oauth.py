"""Shared OAuth 2.0 token helper for adapters (client credentials and password grants). Not an adapter: skipped by
discovery because the module name starts with "_".

    tok = TokenCache()
    h = {"Authorization": "Bearer " + tok.client_credentials(TOKEN_URL, cid, secret, audience="https://api.x/")}

TokenCache(json=True) sends the token request as JSON instead of a form. Tokens are cached in memory per (token URL, grant, client, user, scope, audience) until 60 s before `expires_in`
(or for 5 minutes when the server gives none). Secrets are never logged or stored beyond the process.
"""
import base64
import threading
import time

from .. import http
from .base import MissingCredentials

SKEW_S = 60
DEFAULT_TTL_S = 300


class TokenCache:
    """json=True sends the token request as a JSON body instead of a form (Truepic documents that); everything else,
    caching and refusals included, is the same."""

    def __init__(self, clock=time.monotonic, json=False):
        self._clock = clock
        self.json = bool(json)
        self._lock = threading.Lock()
        self._tokens = {}

    def _fetch(self, key, token_url, form, headers):
        with self._lock:
            hit = self._tokens.get(key)
            if hit and hit[1] > self._clock():
                return hit[0]
        form = {k: v for k, v in form.items() if v is not None}
        try:
            resp = http.post(token_url, body=form, headers=headers) if self.json else http.post(token_url, form=form, headers=headers)
        except http.HTTPError as e:
            if e.status in (400, 401, 403):
                raise MissingCredentials(f"token request to {token_url} was refused (HTTP {e.status}); check the credentials") from None
            raise
        tok = (resp or {}).get("access_token") if isinstance(resp, dict) else None
        if not isinstance(tok, str) or not tok:
            raise MissingCredentials(f"{token_url} returned no access_token")
        ttl = resp.get("expires_in")
        ttl = ttl if isinstance(ttl, (int, float)) and not isinstance(ttl, bool) and ttl > 0 else DEFAULT_TTL_S
        with self._lock:
            self._tokens[key] = (tok, self._clock() + max(ttl - SKEW_S, 1))
        return tok

    def client_credentials(self, token_url, client_id, client_secret, scope=None, audience=None, basic=False, extra=None):
        """Client-credentials grant. basic=True sends the client id and secret as HTTP Basic (RFC 6749 2.3.1); otherwise
        they go in the form body."""
        if not client_id or not client_secret:
            raise MissingCredentials(f"client id and secret are needed for {token_url}")
        form = {"grant_type": "client_credentials", "scope": scope, "audience": audience, **(extra or {})}
        headers = {}
        if basic:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        else:
            form.update(client_id=client_id, client_secret=client_secret)
        return self._fetch((token_url, "cc", client_id, None, scope, audience), token_url, form, headers)

    def password(self, token_url, client_id, username, password, client_secret=None, scope=None, audience=None):
        """Resource-owner password grant (UP42, Vantor Hub document it)."""
        if not username or not password:
            raise MissingCredentials(f"a username and password are needed for {token_url}")
        form = {"grant_type": "password", "client_id": client_id, "client_secret": client_secret, "username": username,
                "password": password, "scope": scope, "audience": audience}
        return self._fetch((token_url, "pw", client_id, username, scope, audience), token_url, form, {})

    def clear(self):
        with self._lock:
            self._tokens.clear()
