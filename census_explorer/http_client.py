"""Explicit HTTP retrieval.

Nothing here runs at import time.  Every function must be called from a
retrieval command.  The module never reads a credential on its own: a caller
passes one in, and the caller is responsible for having obtained it from the
local environment.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable

from .redact import RedactedError, redact

USER_AGENT = "census-explorer/0.1 (local research tool; stdlib urllib)"
DEFAULT_TIMEOUT = 120


class NetworkDisabled(RuntimeError):
    """Raised when code that must stay offline attempts a fetch."""


#: Flipped on by the test suite and by the local server process.  Any attempt
#: to reach the network while this is set fails loudly instead of silently
#: downloading during a test or a render.
_OFFLINE = False


def set_offline(offline: bool = True) -> None:
    global _OFFLINE
    _OFFLINE = bool(offline)


def is_offline() -> bool:
    return _OFFLINE


@dataclass
class Response:
    url_sanitized: str
    status: int
    body: bytes
    headers: dict[str, str]


def build_url(base: str, params: dict[str, str] | None = None) -> str:
    if not params:
        return base
    return base + ("&" if "?" in base else "?") + urllib.parse.urlencode(params)


def sanitize_url(url: str) -> str:
    """Return a URL safe to log: credential parameters are stripped entirely."""
    parts = urllib.parse.urlsplit(url)
    if not parts.query:
        return redact(url)
    kept = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in {"key", "api_key", "apikey", "token", "access_token"}
    ]
    query = urllib.parse.urlencode(kept)
    return redact(urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, "")))


def _request(url: str, timeout: int) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def fetch(url: str, *, timeout: int = DEFAULT_TIMEOUT, retries: int = 3,
          backoff: float = 2.0) -> Response:
    """Fetch a URL into memory.  Errors are redacted before they propagate."""
    if _OFFLINE:
        raise NetworkDisabled(
            f"network access is disabled in this context (wanted {sanitize_url(url)})"
        )
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(_request(url, timeout), timeout=timeout) as resp:
                body = resp.read()
                return Response(
                    url_sanitized=sanitize_url(url),
                    status=resp.status,
                    body=body,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                )
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            raise RedactedError(
                f"HTTP {exc.code} from {sanitize_url(url)}: {exc.reason}"
            ) from None
        except Exception as exc:  # pragma: no cover - network path
            last = exc
            if attempt + 1 < retries:
                time.sleep(backoff * (2 ** attempt))
    raise RedactedError(f"request to {sanitize_url(url)} failed: {last}")


def stream_lines(url: str, *, timeout: int = DEFAULT_TIMEOUT,
                 on_chunk: Callable[[bytes], None] | None = None) -> Iterable[bytes]:
    """Yield raw lines from a large remote text file without buffering it all.

    ``on_chunk`` receives every raw chunk so the caller can hash the complete
    upstream document even though only a subset of lines is retained.
    """
    if _OFFLINE:
        raise NetworkDisabled(
            f"network access is disabled in this context (wanted {sanitize_url(url)})"
        )
    with urllib.request.urlopen(_request(url, timeout), timeout=timeout) as resp:
        remainder = b""
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            if on_chunk is not None:
                on_chunk(chunk)
            buf = remainder + chunk
            *lines, remainder = buf.split(b"\n")
            for line in lines:
                yield line
        if remainder:
            yield remainder
