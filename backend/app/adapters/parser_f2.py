"""f2-backed parser adapter: douyin / weibo / tiktok via the f2 library.

Implements :class:`app.adapters.protocols.ParserAdapter` against the `f2`
library (PyPI ``f2==0.0.1.7``, Python >= 3.10). f2 is an async, cookie-aware
multi-platform crawler; this adapter is the sync facade the rest of the app
expects (FastAPI runs it in the threadpool, so each URL runs in its own
``asyncio.run`` loop — no shared loop across URLs).

Platform contract (verified against f2 0.0.1.7, 2026-08-26):

* **douyin** — ``AwemeIdFetcher.get_aweme_id(url)`` resolves share URLs
  (``/video/{id}``, ``/note/{id}``, short ``v.douyin.com/...``) then
  ``DouyinHandler(kwargs).fetch_one_video(aweme_id)`` returns a
  ``PostDetailFilter`` with ``desc``/``cover``/``duration`` (ms)/
  ``video_play_addr`` (URL list)/``images`` (图文 URL list)/``nickname``.
  A cookie is REQUIRED (f2's crawler reads ``kwargs["cookie"]``; douyin
  rejects requests without a valid one).
* **weibo** — ``WeiboIdFetcher.get_weibo_id(url)`` then
  ``WeiboHandler(kwargs).fetch_one_weibo(weibo_id)`` → ``WeiboDetailFilter``
  (``weibo_desc``/``nickname``/``playback_list`` video URLs/``pic_infos``
  images/``error_code``). Public posts parse without a cookie;
  ``error_code == 20112`` means the post needs a cookie.
* **tiktok** — ``AwemeIdFetcher.get_aweme_id(url)`` (tiktok app) then
  ``TiktokHandler(kwargs).fetch_one_video(itemId=...)`` → filter with
  ``desc``/``video_playAddr``/``video_cover``/``video_duration`` (ms)/
  ``nickname``/``api_status_code``.

Classification (unchanged from the parse-video-py era): a playable video URL
→ ``VIDEO`` (first URL stored in ``metadata["video_url"]``); 图集 image
albums (no video URL, ``images`` present) → ``IMAGE`` (first image's
extension as format, full list in ``metadata["images"]``); neither → typed
:class:`~app.adapters.engine_errors.EngineParseError`. The v1 downloader
streams ``metadata["video_url"]`` and downloads the first image.

Cookie handling (Task 9 wires the classification):

* The adapter reads the platform cookie from the injected
  :class:`~app.adapters.protocols.CookieProvider` per parse.
* douyin/tiktok with NO configured cookie fail fast (no network call) with
  :class:`CookieMissingError`.
* Invalid/expired cookies surface as f2 ``APIUnauthorizedError``, a weibo
  ``error_code == 20112``, or a non-zero douyin/tiktok ``status_code`` —
  mapped to :class:`CookieInvalidError` ("Cookie 无效或已过期，请重新设置").
  The status-code classification is a documented best-effort heuristic: the
  platform's own signals are the source of truth; everything else falls
  through to the existing typed errors.

Errors: f2 exceptions translate to the
:mod:`app.adapters.engine_errors` hierarchy (see :func:`_translate_f2_error`);
the original exception is preserved as ``__cause__``.

Known limitations:

* **Lazy f2 imports.** f2's app modules generate ``msToken``/``ttwid`` by
  calling external token APIs at *module import* time (httpx, ~10 s timeouts).
  Importing them at module load would make this adapter's import unbounded and
  untestable, so the fetch methods resolve f2 classes lazily via
  :meth:`F2ParserAdapter._import_f2` (cached in ``sys.modules`` after the
  first parse of a platform). Each import runs via ``await asyncio.to_thread(...)``
  in a worker thread, so a slow import never stalls the event loop and the
  caller's ``wait_for`` deadline applies to it. Consequences: the first parse
  of each platform pays the token-API latency once; and when a token API is
  unreachable (e.g. tiktok's ``mssdk-sg.tiktok.com`` is not reachable from
  mainland-China networks), that platform's import raises f2's
  ``APITimeoutError``, which the adapter translates to a typed parse-timeout
  failure — the other platforms are unaffected.
* The ID-fetchers resolve short links with f2's own default client
  configuration (f2's ``ClientConfManager``), so ``engine_proxy`` is not
  applied to that one redirect-resolution hop — only to the handler's API calls.
"""

from __future__ import annotations

import asyncio
import importlib
import uuid
from urllib.parse import urlsplit

import httpx

from app.adapters.engine_errors import (
    CookieInvalidError,
    CookieMissingError,
    EngineError,
    EngineNetworkError,
    EngineParseError,
    EngineTimeoutError,
    PlatformBlockedError,
    UnsupportedPlatformError,
    translate_engine_exception,
)
from app.adapters.engine_utils import extension_of, probe_file_size_mb
from app.adapters.protocols import CookieProvider, ParserAdapter
from app.domain import MediaType, ParseCommand, ParseResult, format_duration

__all__ = ["F2ParserAdapter", "_route", "_translate_f2_error"]

_MESSAGE_UNSUPPORTED = "该平台暂不支持链接解析 / Platform URL parsing unsupported"
_MESSAGE_MISSING_COOKIE = (
    "该平台需要 Cookie，请先在设置中配置 / "
    "This platform requires a cookie — configure it in Settings"
)
_MESSAGE_INVALID_COOKIE = (
    "Cookie 无效或已过期，请重新设置 / "
    "Cookie invalid or expired — please update it"
)
_MESSAGE_NO_MEDIA = (
    "解析失败：未获取到视频或图片地址 / No video or image URL resolved"
)
_MESSAGE_TIMEOUT = "解析超时 / Parse timeout"
_MESSAGE_NETWORK = "网络错误 / Network error"
_MESSAGE_BLOCKED = (
    "平台风控，请求被拦截 / Platform anti-scraping blocked the request"
)
_MESSAGE_PARSE_FAILED = "解析失败 / Parse failed"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
)

# Host fragment -> canonical platform. Subdomain matching: ``v.douyin.com``
# endswith ``.douyin.com``; the exact host also matches.
_ROUTES: list[tuple[str, str]] = [
    ("douyin.com", "douyin"),
    ("iesdouyin.com", "douyin"),
    ("weibo.com", "weibo"),
    ("weibo.cn", "weibo"),
    ("tiktok.com", "tiktok"),
]

# Platforms whose crawler REQUIRES a cookie (fail fast when none configured).
_COOKIE_REQUIRED = frozenset({"douyin", "tiktok"})

# Best-effort classifier: douyin/tiktok API status codes commonly returned
# when the request is rejected for cookie/verify reasons. The authoritative
# signals are f2's APIUnauthorizedError and weibo error_code 20112; this set
# covers the douyin 4010 "参数错误/verify" family and tiktok equivalents.
_COOKIE_STATUS_CODES = frozenset({4003, 4010, 4012})

_REFERERS = {
    "douyin": "https://www.douyin.com/",
    "weibo": "https://weibo.com/",
    "tiktok": "https://www.tiktok.com/",
}


def _route(url: str) -> str | None:
    """Return the f2 platform for ``url``, or ``None`` when not a f2 URL.

    Matches the hostname exactly or as a ``.``-suffixed subdomain, so
    unrelated hosts can never false-positive into a platform route.
    """
    host = (urlsplit(url).hostname or "").lower()
    for domain, platform in _ROUTES:
        if host == domain or host.endswith("." + domain):
            return platform
    return None


def _translate_f2_error(exc: BaseException, *, url: str) -> EngineError:
    """Map an f2 exception onto the engine_errors hierarchy.

    f2 wraps its HTTP layer in ``f2.exceptions`` API* classes (all subclasses
    of :class:`APIError`, which carries an optional ``status_code``). The
    authoritative cookie signal (``APIUnauthorizedError``) is mapped first;
    everything else falls back to the generic translation so the two engines
    share the same typed surface. ``url`` is kept for future logging detail;
    it is never embedded in messages. The f2 exceptions module is imported
    lazily (safe — no network) so this module stays importable without f2.
    """
    try:
        from f2.exceptions import (
            APIError,
            APIConnectionError,
            APIRateLimitError,
            APITimeoutError,
            APIUnauthorizedError,
        )
    except ImportError:  # f2 not installed: fall through to generic translation
        return translate_engine_exception(exc, url=url, operation="parse")
    if isinstance(exc, APIUnauthorizedError):
        return CookieInvalidError(_MESSAGE_INVALID_COOKIE)
    if isinstance(exc, APITimeoutError):
        return EngineTimeoutError(_MESSAGE_TIMEOUT)
    if isinstance(exc, APIConnectionError):
        return EngineNetworkError(_MESSAGE_NETWORK)
    if isinstance(exc, APIRateLimitError):
        return PlatformBlockedError(_MESSAGE_BLOCKED)
    if isinstance(exc, APIError):
        # Remaining f2 API errors (response/filter/not-found/retry) are
        # generic parse failures; keep the exception class name as a hint.
        return EngineParseError(f"{_MESSAGE_PARSE_FAILED} ({type(exc).__name__})")
    return translate_engine_exception(exc, url=url, operation="parse")


class F2ParserAdapter:
    """Real :class:`ParserAdapter`: douyin/weibo/tiktok via f2, cookie-aware."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        proxy: str | None = None,
        cookie_provider: CookieProvider | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._proxy = proxy
        self._cookie_provider = cookie_provider
        self._transport = transport  # test seam; None = real network

    # -- protocol ----------------------------------------------------------

    def parse(self, command: ParseCommand) -> list[ParseResult]:
        return [self._parse_one(url) for url in command.urls]

    def _parse_one(self, url: str) -> ParseResult:
        platform = _route(url)
        if platform is None:
            raise UnsupportedPlatformError(_MESSAGE_UNSUPPORTED)
        cookie = (
            self._cookie_provider.get(platform) if self._cookie_provider else None
        )
        if platform in _COOKIE_REQUIRED and not cookie:
            raise CookieMissingError(_MESSAGE_MISSING_COOKIE)
        try:
            if platform == "douyin":
                data = self._run(self._fetch_douyin(url, cookie or ""))
                return self._map_douyin(data, url)
            if platform == "weibo":
                data = self._run(self._fetch_weibo(url, cookie or ""))
                return self._map_weibo(data, url)
            data = self._run(self._fetch_tiktok(url, cookie or ""))
            return self._map_tiktok(data, url)
        except EngineError:
            raise
        except Exception as exc:
            raise _translate_f2_error(exc, url=url) from exc

    # -- sync facade over async f2 ----------------------------------------

    def _import_f2(self, module_name: str, attr_name: str):
        """Resolve an f2 module attribute lazily (once per process).

        f2's app modules generate msToken/ttwid at import time (external token
        APIs with ~10 s httpx timeouts), so importing at module load would make
        this adapter's import unbounded and untestable. Importing here defers
        the cost to the first parse of that platform (then cached by
        ``sys.modules``) and lets tests stub this method instead of the real
        f2 modules. Raises the underlying import error (e.g. f2's
        ``APITimeoutError`` when a token API is unreachable), which the caller
        translates to a typed failure.
        """
        module = importlib.import_module(module_name)
        return getattr(module, attr_name)

    def _run(self, coro):
        """Await an f2 coroutine with the configured timeout in a fresh loop."""
        return asyncio.run(asyncio.wait_for(coro, timeout=self._timeout))

    def _kwargs(self, platform: str, cookie: str) -> dict:
        """Build the f2 handler kwargs (headers/cookie/proxies)."""
        proxies = (
            {"http://": self._proxy, "https://": self._proxy}
            if self._proxy
            else {"http://": None, "https://": None}
        )
        return {
            "headers": {"User-Agent": _USER_AGENT, "Referer": _REFERERS[platform]},
            "cookie": cookie,
            "proxies": proxies,
        }

    # -- per-platform fetchers (async) -------------------------------------

    async def _fetch_douyin(self, url: str, cookie: str):
        # Imports run in a worker thread: f2's module import can block on its
        # token APIs, and a sync import would stall the event loop so the
        # wait_for deadline could never fire.
        AwemeIdFetcher = await asyncio.to_thread(
            self._import_f2, "f2.apps.douyin.utils", "AwemeIdFetcher"
        )
        DouyinHandler = await asyncio.to_thread(
            self._import_f2, "f2.apps.douyin.handler", "DouyinHandler"
        )
        aweme_id = await AwemeIdFetcher.get_aweme_id(url)
        return await DouyinHandler(self._kwargs("douyin", cookie)).fetch_one_video(
            aweme_id
        )

    async def _fetch_weibo(self, url: str, cookie: str):
        # Imports run in a worker thread: f2's module import can block on its
        # token APIs, and a sync import would stall the event loop so the
        # wait_for deadline could never fire.
        WeiboIdFetcher = await asyncio.to_thread(
            self._import_f2, "f2.apps.weibo.utils", "WeiboIdFetcher"
        )
        WeiboHandler = await asyncio.to_thread(
            self._import_f2, "f2.apps.weibo.handler", "WeiboHandler"
        )
        weibo_id = await WeiboIdFetcher.get_weibo_id(url)
        return await WeiboHandler(self._kwargs("weibo", cookie)).fetch_one_weibo(
            weibo_id
        )

    async def _fetch_tiktok(self, url: str, cookie: str):
        # Imports run in a worker thread: f2's module import can block on its
        # token APIs, and a sync import would stall the event loop so the
        # wait_for deadline could never fire.
        TiktokAwemeIdFetcher = await asyncio.to_thread(
            self._import_f2, "f2.apps.tiktok.utils", "AwemeIdFetcher"
        )
        TiktokHandler = await asyncio.to_thread(
            self._import_f2, "f2.apps.tiktok.handler", "TiktokHandler"
        )
        item_id = await TiktokAwemeIdFetcher.get_aweme_id(url)
        return await TiktokHandler(self._kwargs("tiktok", cookie)).fetch_one_video(
            itemId=item_id
        )

    # -- per-platform mapping (Tasks 7-8) ----------------------------------

    def _map_douyin(self, data, url: str) -> ParseResult:
        """Map a douyin PostDetailFilter onto a ParseResult (Task 7).

        A non-zero ``api_status_code`` with a configured cookie means the
        request was rejected (cookie expired / risk control) → CookieInvalidError
        (see Task 9 for the full classifier). ``nickname is None`` is f2's own
        "接口内容异常" signal → EngineParseError. Play URLs and images are
        filtered to usable strings (f2's list helpers None-fill missing paths).
        """
        if data.api_status_code not in (None, 0):
            raise CookieInvalidError(_MESSAGE_INVALID_COOKIE)
        if data.nickname is None:
            raise EngineParseError(_MESSAGE_NO_MEDIA)
        play_urls = [
            url_entry for url_entry in (data.video_play_addr or [])
            if isinstance(url_entry, str) and url_entry
        ]
        video_url = play_urls[0] if play_urls else None
        images = [
            image_url for image_url in (data.images or [])
            if isinstance(image_url, str) and image_url
        ]
        return self._build_result(
            url=url,
            platform="douyin",
            title=(data.desc or "").strip() or "douyin",
            cover=data.cover or None,
            duration_ms=data.duration,
            video_url=video_url,
            images=images,
            author={"uid": data.uid, "name": data.nickname, "avatar": None},
        )

    def _map_weibo(self, data, url: str) -> ParseResult:
        """Map a weibo WeiboDetailFilter onto a ParseResult (Task 7).

        ``error_code == 20112`` is weibo's own "无查看权限，请配置Cookie" signal →
        CookieInvalidError. Public posts parse without a cookie. Play URLs are
        filtered to usable strings (f2's list helpers None-fill missing paths).
        """
        if data.error_code == 20112:
            raise CookieInvalidError(_MESSAGE_INVALID_COOKIE)
        play_urls = [
            url_entry for url_entry in (data.playback_list or [])
            if isinstance(url_entry, str) and url_entry
        ]
        video_url = play_urls[0] if play_urls else None
        images = self._weibo_images(data)
        title = (data.weibo_desc or data.desc or "").strip() or "weibo"
        return self._build_result(
            url=url,
            platform="weibo",
            title=title,
            cover=images[0] if images else None,
            duration_ms=None,  # weibo exposes no duration in the detail filter
            video_url=video_url,
            images=images,
            author={"uid": data.uid, "name": data.nickname, "avatar": None},
        )

    @staticmethod
    def _weibo_images(data) -> list[str]:
        """Extract the ordered image URLs from weibo's ``pic_infos`` dict.

        The filter exposes the dict as the public ``weibo_pic_infos`` property
        (the ``_to_raw()`` fallback covers duck-typed fakes). Each entry is
        ``{pic_id: {...}}``; the large image is either the top-level ``url``
        key or the ``large.url`` sub-object. Entries without a usable URL, and
        malformed entries (non-dict, non-str values), are skipped.
        """
        if hasattr(data, "weibo_pic_infos"):
            pics = data.weibo_pic_infos
        else:
            raw = data._to_raw() if hasattr(data, "_to_raw") else {}
            pics = raw.get("pic_infos") or {}
        if not isinstance(pics, dict):
            return []
        urls: list[str] = []
        for entry in pics.values():
            if not isinstance(entry, dict):
                continue
            image_url = entry.get("url")
            if not isinstance(image_url, str):
                large = entry.get("large")
                image_url = large.get("url") if isinstance(large, dict) else None
            if isinstance(image_url, str) and image_url:
                urls.append(image_url)
        return urls

    def _map_tiktok(self, data, url: str) -> ParseResult:
        raise NotImplementedError("tiktok mapping lands in Task 8")

    # -- shared result builder ----------------------------------------------

    def _build_result(
        self,
        *,
        url: str,
        platform: str,
        title: str,
        cover: str | None,
        duration_ms: int | float | None,
        video_url: str | None,
        images: list[str],
        author: dict,
    ) -> ParseResult:
        """Map the extracted fields onto a :class:`ParseResult`.

        A playable video URL wins → VIDEO; otherwise an image album → IMAGE
        (format from the first image's extension); neither raises a typed
        parse failure. ``duration`` is converted from f2's milliseconds to the
        PRD ``MM:SS`` display form. Field normalization: a blank ``title``
        falls back to the platform name (``ParseResult.title`` is non-blank),
        an empty ``cover`` becomes ``None`` (``ParseResult.cover`` has
        ``min_length=1``), and non-string image URLs are dropped so a bad
        element cannot crash ``extension_of`` or flow into the downloader.
        """
        if video_url:
            media_type = MediaType.VIDEO
            size_url = video_url
            media_format = extension_of(video_url) or "mp4"
        elif images:
            media_type = MediaType.IMAGE
            size_url = images[0]
            media_format = extension_of(images[0]) or "jpg"
        else:
            raise EngineParseError(_MESSAGE_NO_MEDIA)
        return ParseResult(
            task_id=str(uuid.uuid4()),
            url=url,
            media_type=media_type,
            platform=platform,
            title=(title or "").strip() or platform,
            cover=cover or None,
            duration=(
                format_duration(int(duration_ms // 1000)) if duration_ms else None
            ),
            file_size_mb=probe_file_size_mb(
                size_url,
                timeout_seconds=self._timeout,
                proxy=self._proxy,
                transport=self._transport,
            ),
            format=media_format,
            available_qualities=[],
            available_bitrates=[],
            metadata={
                "engine": "f2",
                "video_url": video_url,
                "images": [
                    {"url": image_url, "live_photo_url": None}
                    for image_url in images
                    if isinstance(image_url, str)
                ],
                "author": author,
            },
            error=None,
        )
