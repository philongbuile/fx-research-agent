"""Hindsight document transport for the trusted simulation host, not agent MCP.

Routes/fields checked against upstream v1.1.0 api/http.py. Exact immutable
documents avoid cross-person graph traversal, consolidation and future leakage.
"""

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # never forward a bearer credential to another destination


class HindsightDocuments:
    def __init__(self, url: str, token: str, *, timeout=5):
        parts = urlsplit(url)
        if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/'):
            raise ValueError("use an origin without credentials, path or query")
        if parts.scheme != 'https' and not (parts.scheme == 'http' and parts.hostname in ('localhost', '127.0.0.1', '::1')):
            raise ValueError("Hindsight requires HTTPS except on loopback")
        if not parts.hostname or not token or not 0 < timeout <= 30:
            raise ValueError("origin, bearer token and bounded timeout required")
        self._base = url.rstrip('/') + '/v1/default/banks/fxra'
        self._token, self._timeout = token, timeout
        self._opener = build_opener(NoRedirect)

    @classmethod
    def from_env(cls):
        return cls(os.environ.get('HINDSIGHT_URL', 'http://127.0.0.1:8888'),
                   os.environ.get('HINDSIGHT_API_KEY', ''))

    def _request(self, path, data=None):
        request = Request(self._base + path,
                          data=json.dumps(data).encode() if data is not None else None,
                          headers={'Authorization': 'Bearer ' + self._token, 'Content-Type': 'application/json'})
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                # Memory documents are small; never consume an unbounded response.
                raw = response.read(256_001)
            if len(raw) > 256_000:
                raise ValueError("oversized Hindsight response")
            return json.loads(raw)
        except (HTTPError, URLError, TimeoutError, ValueError):
            # No raw response, endpoint, request, credentials or personal narrative in errors.
            raise RuntimeError("Hindsight document operation failed; pending delivery preserved") from None

    @staticmethod
    def _document(document):
        if not re.fullmatch(r'sim-[a-f0-9]{32}-(personal|knowledge)-[a-f0-9]{64}', document):
            raise ValueError("simulation document id required")
        return quote(document, safe='')

    def retain(self, document, content, at):
        self._document(document)
        result = self._request('/memories', {'items': [{'document_id': document, 'content': content,
                                                       'timestamp': at, 'tags': ['simulation']}], 'async': False})
        if result.get('success') is not True or result.get('async', False):
            raise RuntimeError("Hindsight retain not synchronously acknowledged")

    def read(self, document):
        result = self._request('/documents/' + self._document(document))
        text = result.get('original_text')
        if result.get('id') != document or result.get('bank_id') != 'fxra' or not isinstance(text, str):
            raise RuntimeError("Hindsight original document unavailable or mismatched")
        return text


class OfflineDocuments:
    """Ephemeral test/demo double. This is not a replacement memory store."""

    def __init__(self):
        self.documents = {}

    def retain(self, document, content, at):
        previous = self.documents.setdefault(document, content)
        if previous != content:
            raise ValueError("immutable document changed")

    def read(self, document):
        return self.documents[document]


def agent_tools(session):
    """RPC boundary: the host binds identity, clock, engine and backend beforehand.

    Export only this dispatcher to an untrusted agent with no shell/filesystem,
    Hindsight credential, raw MCP, database, or Python object introspection.
    """
    from .simulation import CharacterSession, FinalSession

    methods = ('read', 'remember', 'propose') if isinstance(session, CharacterSession) else ('read', 'submit')
    if not isinstance(session, (CharacterSession, FinalSession)):
        raise TypeError("identity-bound session required")
    allowed = {name: getattr(session, name) for name in methods}

    def dispatch(method, arguments=None):
        if method not in allowed:
            raise PermissionError("tool not granted")
        return allowed[method](**(arguments or {}))

    return dispatch
