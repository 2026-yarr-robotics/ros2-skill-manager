"""api_client — minimal HTTP client used by every skill.

The YARR API sits behind Cloudflare, whose Browser Integrity Check (HTTP 403,
error 1010) rejects the default `Python-urllib` User-Agent.  We use a single
shared `Request` builder so every skill carries a browser-shaped UA without
re-discovering this gotcha.

Calls run on the caller's thread; skills should fire them in a daemon thread
so the tkinter UI never blocks.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


_DEFAULT_HEADERS: dict[str, str] = {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9',
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36 '
        'skill_manager/0.1'
    ),
}


# Vision-node slot key → YARR API slot key.  The verifier produces
# L1_L/L1_M/L1_R/L2_L/L2_R/L3_T (human-friendly); the /skill/pyramid endpoint
# expects 1l/1m/1r/2l/2r/3m.
SLOT_KEY_TO_API: dict[str, str] = {
    'L1_L': '1l', 'L1_M': '1m', 'L1_R': '1r',
    'L2_L': '2l', 'L2_R': '2r',
    'L3_T': '3m',
}
SLOT_KEY_FROM_API: dict[str, str] = {v: k for k, v in SLOT_KEY_TO_API.items()}


@dataclass
class ApiResult:
    """Either a parsed JSON body (`data`) or an error description.

    `kind`:
      • 'ok'        → 2xx, JSON parsed into `data` (dict / list / scalar)
      • 'api_fail'  → 2xx but body contained {"success": false, ...}
      • 'http'      → non-2xx HTTP status (code in `code`, body excerpt in `detail`)
      • 'network'   → URLError / timeout (reason in `detail`)
      • 'parse'     → 2xx but body wasn't valid JSON (raw in `detail`)
    """
    kind: str
    code: int | None = None
    data: Any = None
    detail: str = ''

    @property
    def ok(self) -> bool:
        return self.kind == 'ok'

    @property
    def short(self) -> str:
        """One-line summary suitable for a status bar."""
        if self.kind == 'ok':
            return 'OK'
        if self.kind == 'api_fail':
            return f'API success=false ({self.detail or "no detail"})'
        if self.kind == 'http':
            return f'HTTP {self.code} {self.detail[:80]}'
        if self.kind == 'network':
            return f'unreachable ({self.detail})'
        if self.kind == 'parse':
            return f'invalid JSON ({self.detail[:80]})'
        return self.kind


def _build_request(method: str, url: str, payload: Any) -> urllib.request.Request:
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
    return urllib.request.Request(
        url, data=data, method=method, headers=dict(_DEFAULT_HEADERS))


def call(method: str, url: str, *,
         payload: Any = None, timeout_s: float = 15.0) -> ApiResult:
    """Synchronous JSON HTTP call.  Returns ApiResult; never raises."""
    try:
        req = _build_request(method, url, payload)
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return ApiResult(
            kind='http', code=e.code,
            detail=e.read().decode('utf-8', 'replace')[:200])
    except urllib.error.URLError as e:
        return ApiResult(kind='network', detail=str(e.reason))
    except Exception as e:  # noqa: BLE001 — defensive blanket
        return ApiResult(kind='network', detail=repr(e))

    if not body.strip():
        return ApiResult(kind='ok', code=200, data=None)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        return ApiResult(kind='parse', code=200, detail=f'{e}: {body[:100]}')

    # Convention: a response with success=False is an API-level failure even
    # though HTTP succeeded.  Skills can still inspect `data` if they want.
    if isinstance(data, dict) and data.get('success') is False:
        detail = str(data.get('detail') or data.get('skill') or '')
        return ApiResult(kind='api_fail', code=200, data=data, detail=detail)

    return ApiResult(kind='ok', code=200, data=data)


def post(url: str, payload: Any, *, timeout_s: float = 15.0) -> ApiResult:
    return call('POST', url, payload=payload, timeout_s=timeout_s)


def get(url: str, *, timeout_s: float = 5.0) -> ApiResult:
    return call('GET', url, payload=None, timeout_s=timeout_s)
