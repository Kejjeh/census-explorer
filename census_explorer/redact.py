"""Credential redaction.

Every string that can reach a log, manifest, export, error message, or the
browser passes through :func:`redact`.  The rule is deliberately blunt: any
value of a known credential environment variable, and any ``key=`` query
parameter, is replaced before the string leaves the process.
"""

from __future__ import annotations

import os
import re
from typing import Iterable

#: Environment variables whose values must never be emitted.
CREDENTIAL_ENV_VARS = ("CENSUS_API_KEY", "IPUMS_API_KEY")

REDACTED = "[REDACTED]"

# ``key=...`` / ``api_key=...`` / ``token=...`` inside a URL or query string.
_QUERY_KEY_RE = re.compile(
    r"(?i)\b(key|api_key|apikey|token|access_token)=([^&\s\"']*)"
)


def credential_values(environ: dict | None = None) -> list[str]:
    """Return the non-empty values of known credential variables."""
    env = os.environ if environ is None else environ
    out = []
    for name in CREDENTIAL_ENV_VARS:
        value = env.get(name)
        if value and value.strip():
            out.append(value.strip())
    return out


def redact(text: object, extra_secrets: Iterable[str] = ()) -> str:
    """Return ``text`` as a string with credentials removed.

    Redaction is applied twice over: literal secret values (so a key pasted
    anywhere is caught) and ``key=`` style query parameters (so a key supplied
    by another route is caught even when it is not in this process's
    environment).
    """
    s = text if isinstance(text, str) else repr(text)
    for secret in list(credential_values()) + [x for x in extra_secrets if x]:
        if secret:
            s = s.replace(secret, REDACTED)
    s = _QUERY_KEY_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", s)
    return s


def redact_structure(obj, extra_secrets: Iterable[str] = ()):
    """Recursively redact strings inside dicts/lists/tuples."""
    if isinstance(obj, str):
        return redact(obj, extra_secrets)
    if isinstance(obj, dict):
        return {k: redact_structure(v, extra_secrets) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_structure(v, extra_secrets) for v in obj]
    return obj


class RedactedError(RuntimeError):
    """An error whose message is guaranteed to be redacted."""

    def __init__(self, message: object):
        super().__init__(redact(message))
