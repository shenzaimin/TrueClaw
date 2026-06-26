from __future__ import annotations

import hashlib
import hmac
import time


def verify_slack_signature(
    signing_secret: str,
    body: bytes,
    ts: str | None,
    sig: str | None,
    *,
    skew_sec: int = 300,
) -> bool:
    if not signing_secret:
        return True
    if ts is None or sig is None:
        return False
    try:
        if abs(time.time() - int(ts)) > skew_sec:
            return False
    except ValueError:
        return False
    basestring = f"v0:{ts}:".encode("utf-8") + body
    digest = hmac.new(signing_secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, sig)
