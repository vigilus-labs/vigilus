"""SSRF-safe in-house page fetcher (the default ``fetch_backend``).

Security-critical (see SEARCH_IMPLEMENTATION_PLAN.md §7 and CLAUDE.md). The
fetcher:

* rejects non-http(s) schemes;
* resolves the hostname and refuses loopback / RFC1918 / link-local / ULA /
  ``0.0.0.0/8`` / cloud-metadata addresses unless ``allow_private`` is set;
* re-validates after **every** redirect (a public URL can 302 to an internal
  one) and caps the redirect chain;
* enforces a hard byte cap (stops reading the stream past it) and a timeout;
* strips ``<script>``/``<style>``, extracts visible text, and truncates.

Fails closed: any error in scheme/IP validation denies the fetch.
"""

from __future__ import annotations

import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
import structlog

from vigilus.search.base import FetchBackend, FetchedPage, SearchError

logger = structlog.get_logger(__name__)

# Cloud metadata endpoints that must never be reachable, even if DNS or a
# redirect points at them (covers AWS/GCP/Azure IMDS and the IPv6 variant).
_METADATA_HOSTS = frozenset({"169.254.169.254", "fd00:ec2::254"})

# Cap how many redirects we follow; each hop is re-validated.
_MAX_REDIRECTS = 3

# Truncate extracted text to a token-ish budget before handing it to the model.
_MAX_TEXT_CHARS = 40_000


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if *ip* is in a range we must never fetch from."""
    if str(ip) in _METADATA_HOSTS:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_public_url(
    url: str,
    *,
    allow_private: bool = False,
    allowed_schemes: tuple[str, ...] | list[str] = ("http", "https"),
) -> str:
    """Validate *url* for SSRF safety. Returns the hostname or raises SearchError.

    Resolves the hostname and rejects any URL that maps (in whole or part) to a
    private / loopback / link-local / metadata address. Fails closed: a DNS
    failure or any unexpected error is treated as "block".
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:  # noqa: BLE001
        raise SearchError(f"Could not parse URL: {url}") from exc

    scheme = (parsed.scheme or "").lower()
    if scheme not in allowed_schemes:
        raise SearchError(
            f"Blocked scheme '{scheme or '(none)'}': only {', '.join(allowed_schemes)} are allowed."
        )

    host = parsed.hostname
    if not host:
        raise SearchError("URL has no host.")

    if allow_private:
        return host

    # A literal IP host: validate directly.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked_ip(literal):
            raise SearchError(
                f"Blocked address {host}: internal/private/metadata fetch is disabled."
            )
        return host

    # Hostname: resolve every address it maps to and block if ANY is internal.
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if scheme == "https" else 80))
    except socket.gaierror as exc:
        raise SearchError(f"Could not resolve host '{host}'.") from exc
    except Exception as exc:  # noqa: BLE001 — fail closed
        raise SearchError(f"Host validation failed for '{host}'.") from exc

    if not infos:
        raise SearchError(f"Host '{host}' did not resolve to any address.")

    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise SearchError(f"Host '{host}' resolved to an unparseable address.")
        if _is_blocked_ip(ip):
            raise SearchError(
                f"Blocked: host '{host}' resolves to internal address {ip_str}. "
                "Internal/private fetch is disabled."
            )

    return host


class _TextExtractor(HTMLParser):
    """Minimal HTML→text: drops <script>/<style>, keeps visible text + <title>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self.title: str | None = None
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in ("script", "style", "noscript", "template"):
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "template") and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title = (self.title or "") + data
            return
        text = data.strip()
        if text:
            self._chunks.append(text)

    def get_text(self) -> str:
        # Collapse runs of whitespace/newlines into a tidy block.
        raw = " ".join(c if c == "\n" else c for c in self._chunks)
        lines = [ln.strip() for ln in raw.split("\n")]
        return "\n".join(ln for ln in lines if ln).strip()


def html_to_text(html: str) -> tuple[str | None, str]:
    """Return (title, cleaned_text) from raw HTML using only the stdlib."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 — never let a malformed page break the fetch
        pass
    title = (parser.title or "").strip() or None
    return title, parser.get_text()


class BuiltinFetchBackend(FetchBackend):
    """In-house httpx fetcher with SSRF protection and size/time caps."""

    name = "builtin"

    def __init__(
        self,
        *,
        allow_private: bool = False,
        allowed_schemes: tuple[str, ...] | list[str] = ("http", "https"),
        max_bytes: int = 2_000_000,
        timeout_seconds: int = 15,
    ) -> None:
        self.allow_private = allow_private
        self.allowed_schemes = tuple(allowed_schemes)
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds

    async def fetch(self, url: str) -> FetchedPage:
        current = url
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,  # we follow manually so each hop is re-validated
        ) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                validate_public_url(
                    current,
                    allow_private=self.allow_private,
                    allowed_schemes=self.allowed_schemes,
                )
                try:
                    async with client.stream("GET", current) as resp:
                        # Re-validate redirect targets before following them.
                        if resp.is_redirect:
                            location = resp.headers.get("location")
                            if not location:
                                raise SearchError("Redirect with no Location header.")
                            current = str(httpx.URL(current).join(location))
                            continue

                        body = bytearray()
                        async for chunk in resp.aiter_bytes():
                            body.extend(chunk)
                            if len(body) >= self.max_bytes:
                                logger.info(
                                    "web_fetch.truncated_bytes",
                                    url=str(resp.url),
                                    cap=self.max_bytes,
                                )
                                break
                        html = body.decode(resp.encoding or "utf-8", errors="replace")
                        title, text = html_to_text(html)
                        if len(text) > _MAX_TEXT_CHARS:
                            text = text[:_MAX_TEXT_CHARS] + "\n…[truncated]"
                        return FetchedPage(
                            url=str(resp.url),
                            title=title,
                            text=text,
                            status_code=resp.status_code,
                        )
                except httpx.HTTPError as exc:
                    raise SearchError(f"Fetch failed: {exc}") from exc

        raise SearchError(f"Too many redirects (>{_MAX_REDIRECTS}).")
