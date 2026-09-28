"""Web search and page-fetching primitives for the WebSearch/WebFetch tools.

Deliberately NOT listed in local_tools._discover_module_tools: nothing here is
exposed as an LLM tool, it is internal plumbing for those two tools.
"""

import asyncio
import io
import ipaddress
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import trafilatura
from curl_cffi.requests import AsyncSession
from ddgs import DDGS
from pypdf import PdfReader

logger = logging.getLogger(__name__)

SEARCH_RESULTS = 5

# Search engines, queried in PARALLEL and merged into a single ranked list of
# SEARCH_RESULTS rows. Measured over 36 queries (en/es/news): 0.6s median
# against 1.5-6.1s for the ddgs "auto" default, which fans out to every engine
# and pays for the slowest one. Both engines together cost barely more than
# yandex alone, so one going down or blocking degrades the blend instead of
# emptying it. "auto" is deliberately NOT in this list: ddgs expands it to every
# engine and shuffles the order, which would undo the speed.
SEARCH_BACKENDS = 'yandex,bing'

# Rendered in the browser: the HTML we get back is navigation chrome, not content.
UNFETCHABLE_DOMAINS = (
    'youtube.com',
    'youtu.be',
    'tiktok.com',
    'twitter.com',
    'x.com',
    'pinterest.com',
)

# Overfetch, so dropping the dead ones does not shrink the result set.
SEARCH_CANDIDATES = SEARCH_RESULTS * 3

FETCH_TIMEOUT_S = 8.0
FETCH_DEADLINE_S = 10.0
MIN_PAGE_CHARS = 200
PER_PAGE_CHARS = 6000
TOTAL_CHARS = 20000

# Redirects are followed by hand (see _download), one guard check per hop.
MAX_REDIRECTS = 3
REDIRECT_STATUSES = (301, 302, 303, 307, 308)

# A text page is a few hundred KB; 2 MB is generous and caps the RAM a single
# page can cost the worker.
MAX_BODY_BYTES = 2 * 1024 * 1024

# libcurl's CURL_WRITEFUNC_ERROR, returned from a content_callback to abort the
# transfer. curl_cffi does not export it, and returning anything else (False, 0,
# -1) is ignored: it only warns and keeps downloading.
WRITEFUNC_ERROR = 0xFFFFFFFF


@dataclass(frozen=True)
class Page:
    n: int
    url: str
    title: str
    text: str


def _is_unfetchable(url: str) -> bool:
    host = (urlparse(url).hostname or '').lower()
    return any(host == d or host.endswith('.' + d) for d in UNFETCHABLE_DOMAINS)


def search(query: str) -> list[dict]:
    """Return raw search rows (at least `href` and `title`).

    Single entry point for search: swap the body of this function to change
    search provider. Nothing else in the codebase talks to a search backend.
    """
    try:
        with DDGS() as ddgs:
            rows = list(ddgs.text(query, max_results=SEARCH_CANDIDATES, backend=SEARCH_BACKENDS))
    except Exception:
        logger.warning('search backend failed for query %r', query, exc_info=True)
        return []

    kept: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        href = row.get('href')
        if not href or _is_unfetchable(href):
            continue
        kept.append(row)
        if len(kept) == SEARCH_RESULTS:
            break
    return kept


def _is_public_http_url(url: str) -> bool:
    """True only for http(s) URLs whose every resolved address is globally routable.

    Rejects the private, loopback, link-local and cloud-metadata ranges that make
    a server-side fetch an SSRF primitive. Total: a malformed URL or port is a
    False, never an exception, so callers can treat a non-guard hit as "skip".
    Blocking: call via asyncio.to_thread.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        infos = socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)
        return all(ipaddress.ip_address(info[4][0]).is_global for info in infos)
    except (ValueError, socket.gaierror):
        return False


def _pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


async def _download(session: AsyncSession, url: str) -> tuple[int, str, bytes] | None:
    """GET url, following redirects by hand. Returns (status, content_type, body).

    curl is not allowed to follow redirects itself: a public URL that 302s to a
    private address would be fetched before any guard could see it, so every hop
    is re-checked here before it is requested.
    """
    for _ in range(MAX_REDIRECTS + 1):
        if not await asyncio.to_thread(_is_public_http_url, url):
            return None
        chunks: list[bytes] = []
        size = 0

        def on_chunk(chunk: bytes) -> int:
            nonlocal size
            chunks.append(chunk)
            size += len(chunk)
            # Over budget: stop the transfer, but keep what already arrived.
            return WRITEFUNC_ERROR if size >= MAX_BODY_BYTES else len(chunk)

        try:
            resp = await session.get(
                url,
                timeout=FETCH_TIMEOUT_S,
                allow_redirects=False,
                content_callback=on_chunk,
            )
        except Exception:
            # A tripped byte budget aborts mid-body; a truncated page still beats
            # no page. Zero bytes means the request itself failed.
            if not chunks:
                return None
            return 200, "", b"".join(chunks)

        if resp.status_code in REDIRECT_STATUSES:
            location = resp.headers.get("location", "").strip()
            if not location:
                return None
            url = urljoin(url, location)  # relative Location is normal
            continue
        return (
            resp.status_code,
            resp.headers.get("content-type", "").lower(),
            b"".join(chunks),
        )
    return None  # hop limit exhausted


async def _fetch_with(session: AsyncSession, url: str) -> str | None:
    """Fetch and extract one URL. The caller owns the session."""
    try:
        got = await _download(session, url)
        if not got:
            return None
        status, content_type, body = got
        if status >= 400:
            return None
        if "application/pdf" in content_type:
            text = await asyncio.to_thread(_pdf_text, body)
        else:
            # favor_precision keeps the main content and drops nav/boilerplate.
            # Bounding this work: per-URL timeout, batch deadline, max body bytes.
            # Bytes not str, so the decode happens in the thread, not on the loop.
            text = await asyncio.to_thread(
                trafilatura.extract,
                body,
                favor_precision=True,
                include_comments=False,
            )
    except Exception:
        return None
    if not text or len(text) < MIN_PAGE_CHARS:
        return None
    return text


async def fetch_page(url: str) -> str | None:
    """Return the main text of a single page, or None if it cannot be extracted."""
    async with AsyncSession(impersonate="chrome") as session:
        return await _fetch_with(session, url)


async def fetch_pages(results: list[dict]) -> list[Page]:
    """Fetch every search result concurrently under one shared deadline.

    Returns whatever finished in time, in completion order, capped per page and
    per batch. A batch that hits the deadline is not an error: partial sources
    still make a usable summary.
    """
    titles = {r["href"]: (r.get("title") or r["href"]) for r in results if r.get("href")}
    collected: list[tuple[str, str]] = []

    async def collect(session: AsyncSession, url: str) -> None:
        text = await _fetch_with(session, url)
        if text:
            collected.append((url, text))

    async with AsyncSession(impersonate="chrome") as session:
        tasks = [asyncio.create_task(collect(session, url)) for url in titles]
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=FETCH_DEADLINE_S,
            )
        except asyncio.TimeoutError:
            pass  # keep whatever landed in `collected` before the deadline

    pages: list[Page] = []
    total = 0
    for url, text in collected:
        if total >= TOTAL_CHARS:
            break
        text = text[:PER_PAGE_CHARS]
        total += len(text)
        pages.append(Page(n=len(pages) + 1, url=url, title=titles[url], text=text))
    return pages
