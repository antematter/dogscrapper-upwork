"""
Shared proxy helpers for Colab scrapers (chewy_colab / tractor_colab).

Free public proxy lists are almost always datacenter IPs, not true residential —
but they often work better than raw Google Colab egress for retail sites.

Usage in a notebook:
    from colab_proxy import ProxyConfig, fetch_free_proxies, get_with_proxy_rotation

Or copy the functions into your notebook cell.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urlparse

try:
    from curl_cffi import requests as cffi_requests

    _HAS_CFFI = True
except ImportError:
    cffi_requests = None  # type: ignore
    _HAS_CFFI = False

try:
    import httpx

    _HAS_HTTPX = True
except ImportError:
    httpx = None  # type: ignore
    _HAS_HTTPX = False


@dataclass
class ProxyConfig:
    """User override + auto-discovery settings."""

    proxy_url: str = ""  # e.g. http://user:pass@host:port or socks5://...
    auto_discover: bool = True
    country: str = "US"
    max_proxies_to_try: int = 12
    validate_min_body: int = 8000
    impersonate: str = "chrome124"


# Public HTTP proxy list sources (datacenter; rotate often)
_PROXY_SOURCES = (
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=8000&country={country}",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=8000&country=all",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
)


def _normalize_proxy_url(host: str) -> str:
    host = host.strip()
    if not host:
        return ""
    if host.startswith(("http://", "https://", "socks4://", "socks5://")):
        return host
    return f"http://{host}"


def _proxy_dict(proxy_url: str) -> dict[str, str]:
    p = _normalize_proxy_url(proxy_url)
    return {"http": p, "https": p}


def fetch_free_proxies(country: str = "US", limit: int = 40) -> list[str]:
    """Pull HTTP proxies from public lists; prefer US when filterable."""
    if not _HAS_CFFI and not _HAS_HTTPX:
        print("[proxy] Install curl_cffi or httpx to fetch proxy lists")
        return []

    fetcher = cffi_requests if _HAS_CFFI else httpx
    seen: set[str] = set()
    out: list[str] = []

    for tpl in _PROXY_SOURCES:
        url = tpl.format(country=country.upper())
        try:
            if _HAS_CFFI and fetcher is cffi_requests:
                r = fetcher.get(url, timeout=20, impersonate="chrome124")
                text = r.text
            else:
                r = httpx.get(url, timeout=20, follow_redirects=True)
                text = r.text
        except Exception as e:
            print(f"[proxy] list fetch failed {url[:60]}...: {e}")
            continue

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # host:port or user:pass@host:port
            if re.match(r"^[\w.\-]+:\d+$", line) or "@" in line:
                proxy = _normalize_proxy_url(line)
            elif re.match(r"^\d+\.\d+\.\d+\.\d+:\d+$", line):
                proxy = _normalize_proxy_url(line)
            else:
                continue
            if proxy not in seen:
                seen.add(proxy)
                out.append(proxy)
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break

    random.shuffle(out)
    print(f"[proxy] Collected {len(out)} candidate proxies")
    return out[:limit]


def validate_proxy(
    proxy_url: str,
    test_url: str,
    *,
    min_body: int = 8000,
    impersonate: str = "chrome124",
    accept_codes: tuple[int, ...] = (200, 206),
    reject_substrings: tuple[str, ...] = (
        "access denied",
        "captcha",
        "datadome",
        "no treats",
        "please enable js",
    ),
) -> tuple[bool, int, int]:
    """
    Returns (ok, status_code, body_length).
  ok if status in accept_codes, body long enough, not a known block page.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    proxies = _proxy_dict(proxy_url)
    try:
        if _HAS_CFFI:
            r = cffi_requests.get(
                test_url,
                headers=headers,
                proxies=proxies,
                impersonate=impersonate,
                timeout=25,
            )
        elif _HAS_HTTPX:
            with httpx.Client(
                proxies=proxies,
                follow_redirects=True,
                timeout=25,
                http2=False,
            ) as client:
                r = client.get(test_url, headers=headers)
        else:
            return False, 0, 0

        body = r.text or ""
        bl = len(body)
        low = body[:4000].lower()
        if r.status_code not in accept_codes:
            return False, r.status_code, bl
        if bl < min_body:
            return False, r.status_code, bl
        if any(s in low for s in reject_substrings):
            return False, r.status_code, bl
        return True, r.status_code, bl
    except Exception:
        return False, 0, 0


def find_working_proxy(
    test_url: str,
    config: ProxyConfig,
) -> Optional[str]:
    """Try user proxy first, then auto-discovered list."""
    if config.proxy_url.strip():
        p = _normalize_proxy_url(config.proxy_url)
        ok, status, bl = validate_proxy(
            p,
            test_url,
            min_body=config.validate_min_body,
            impersonate=config.impersonate,
        )
        print(f"[proxy] User proxy → ok={ok} status={status} len={bl}")
        if ok:
            return p

    if not config.auto_discover:
        return None

    candidates = fetch_free_proxies(country=config.country, limit=config.max_proxies_to_try * 3)
    tried = 0
    for proxy in candidates:
        if tried >= config.max_proxies_to_try:
            break
        tried += 1
        ok, status, bl = validate_proxy(
            proxy,
            test_url,
            min_body=config.validate_min_body,
            impersonate=config.impersonate,
        )
        host = urlparse(proxy).hostname or proxy[:30]
        print(f"[proxy] try {tried}/{config.max_proxies_to_try} {host} → ok={ok} status={status} len={bl}")
        if ok:
            return proxy
        time.sleep(random.uniform(0.3, 0.8))

    return None


def get_with_proxy(
    url: str,
    *,
    proxy_url: str,
    headers: Optional[dict[str, str]] = None,
    json_mode: bool = False,
    timeout: int = 40,
    impersonate: str = "chrome124",
) -> Any:
    """Single GET through proxy (curl_cffi preferred)."""
    h = dict(headers or {})
    if json_mode:
        h.setdefault("Accept", "application/json, text/plain, */*")
    proxies = _proxy_dict(proxy_url)
    if _HAS_CFFI:
        return cffi_requests.get(
            url,
            headers=h,
            proxies=proxies,
            impersonate=impersonate,
            timeout=timeout,
        )
    if _HAS_HTTPX:
        with httpx.Client(
            proxies=proxies,
            follow_redirects=True,
            timeout=timeout,
            http2=False,
        ) as client:
            return client.get(url, headers=h)
    raise RuntimeError("Install curl_cffi or httpx")


def get_with_proxy_rotation(
    url: str,
    config: ProxyConfig,
    *,
    headers: Optional[dict[str, str]] = None,
    json_mode: bool = False,
    timeout: int = 40,
    is_success: Optional[Callable[[Any], bool]] = None,
) -> tuple[Optional[Any], Optional[str]]:
    """
    Find a working proxy against `url`, then GET `url` (or call is_success on response).
    Returns (response, proxy_used).
    """
    default_ok = lambda r: r.status_code == 200 and len(r.text or "") >= config.validate_min_body  # noqa: E731

    check = is_success or default_ok

    if config.proxy_url.strip():
        proxies_to_try = [_normalize_proxy_url(config.proxy_url)]
    else:
        proxies_to_try = []

    if config.auto_discover:
        proxies_to_try.extend(
            fetch_free_proxies(country=config.country, limit=config.max_proxies_to_try * 2)
        )

    seen: set[str] = set()
    tried = 0
    for proxy in proxies_to_try:
        if not proxy or proxy in seen:
            continue
        seen.add(proxy)
        if tried >= config.max_proxies_to_try + (1 if config.proxy_url else 0):
            break
        tried += 1
        try:
            r = get_with_proxy(
                url,
                proxy_url=proxy,
                headers=headers,
                json_mode=json_mode,
                timeout=timeout,
                impersonate=config.impersonate,
            )
            if check(r):
                print(f"[proxy] SUCCESS via {urlparse(proxy).hostname}")
                return r, proxy
            print(
                f"[proxy] {urlparse(proxy).hostname}: "
                f"status={r.status_code} len={len(r.text or '')}"
            )
        except Exception as e:
            print(f"[proxy] {urlparse(proxy).hostname}: error {e}")
        time.sleep(random.uniform(0.4, 1.0))

    return None, None
