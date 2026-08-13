"""Shared HTTP behaviour: a polite session with retries and a rate limit.

Public bibliographic APIs are free and maintained by people with budgets.
Hammering them gets your institution's IP range blocked, so: one session,
a real User-Agent with contact details, exponential backoff, and a floor on
the interval between calls.
"""

from __future__ import annotations

import sys
import time
from typing import Any

import requests

from .. import config

_LAST_CALL: dict[str, float] = {}
_SESSION: requests.Session | None = None

# Circuit breaker. If a host refuses to connect repeatedly, it is down, blocked,
# or you are offline — and no amount of retrying will fix that. Without this, a
# sweep on a disconnected laptop spends ten minutes backing off through forty
# queries before telling you the wifi is off.
_FAILURES: dict[str, int] = {}
_TRIPPED: set[str] = set()
FAILURE_LIMIT = 3


def circuit_state() -> dict[str, int]:
    """Hosts that have tripped the breaker this run, with their failure counts."""
    return {h: _FAILURES.get(h, 0) for h in sorted(_TRIPPED)}


def reset_circuits() -> None:
    _FAILURES.clear()
    _TRIPPED.clear()


def session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.headers.update({"User-Agent": config.USER_AGENT, "Accept": "application/json"})
        _SESSION = s
    return _SESSION


def _throttle(host: str, delay: float) -> None:
    last = _LAST_CALL.get(host, 0.0)
    wait = delay - (time.monotonic() - last)
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL[host] = time.monotonic()


def get(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 30,
    delay: float = 0.34,
    retries: int = 3,
    accept: str | None = None,
) -> requests.Response | None:
    """GET with backoff. Returns None instead of raising — callers fail soft."""
    host = url.split("/")[2] if "//" in url else url
    if host in _TRIPPED:
        return None

    headers = {"Accept": accept} if accept else None
    for attempt in range(retries + 1):
        _throttle(host, delay)
        try:
            r = session().get(url, params=params, timeout=timeout, headers=headers)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            _FAILURES.pop(host, None)  # a success closes the circuit
            return r
        except requests.RequestException as e:
            unreachable = isinstance(
                e, (requests.ConnectionError, requests.Timeout, requests.exceptions.ProxyError)
            )
            if attempt == retries or unreachable:
                if unreachable:
                    _FAILURES[host] = _FAILURES.get(host, 0) + 1
                    if _FAILURES[host] >= FAILURE_LIMIT and host not in _TRIPPED:
                        _TRIPPED.add(host)
                        print(
                            f"  ! {host} unreachable {_FAILURES[host]}× — skipping it for "
                            f"the rest of this run (offline, blocked, or the host is down)",
                            file=sys.stderr,
                        )
                        return None
                print(f"  ! {host}: {e}", file=sys.stderr)
                return None
            time.sleep(2 ** attempt)
    return None
