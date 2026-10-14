"""Playwright browser sessions with stealth, fingerprint randomisation and proxy support."""

from __future__ import annotations

import logging
import random
from types import TracebackType
from typing import Any

from app.automation.proxy import ProxyManager, proxy_manager
from app.config import settings

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
]
VIEWPORTS = [(1920, 1080), (1680, 1050), (1536, 864), (1440, 900), (1366, 768), (1600, 900)]

# Minimal stealth patches (navigator.webdriver, plugins, languages, WebGL vendor, chrome runtime).
STEALTH_JS = """
(() => {
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
  Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'Chrome PDF Plugin' })) });
  Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => %(cores)d });
  window.chrome = window.chrome || { runtime: {} };
  const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
  if (originalQuery) {
    window.navigator.permissions.query = (parameters) => (
      parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters)
    );
  }
  const getParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function (parameter) {
    if (parameter === 37445) return '%(vendor)s';
    if (parameter === 37446) return '%(renderer)s';
    return getParameter.call(this, parameter);
  };
})();
"""
WEBGL = [
    ("Intel Inc.", "Intel Iris OpenGL Engine"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Apple Inc.", "Apple M1"),
]


class BrowserUnavailable(Exception):
    pass


class BrowserSession:
    """Context manager wrapping a sync Playwright browser + context.

    Each session is isolated (fresh context, no shared state between users) and ephemeral.
    """

    def __init__(
        self,
        *,
        cookies: list[dict[str, Any]] | None = None,
        storage_state: dict[str, Any] | None = None,
        use_proxy: bool = True,
        headless: bool | None = None,
        proxies: ProxyManager | None = None,
        timezone_id: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.cookies = cookies
        self.user_agent = user_agent  # present as this browser (e.g. the one a synced login belongs to)
        self.storage_state = storage_state
        self.use_proxy = use_proxy
        self.headless = settings.BROWSER_HEADLESS if headless is None else headless
        self.proxies = proxies or proxy_manager
        self.timezone_id = timezone_id
        self.proxy_url: str | None = None
        self._pw: Any = None
        self.browser: Any = None
        self.context: Any = None
        self.page: Any = None

    def __enter__(self) -> BrowserSession:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise BrowserUnavailable("playwright is not installed") from exc
        self._pw = sync_playwright().start()
        self.proxy_url = self.proxies.pick() if self.use_proxy else None
        launch_kwargs: dict[str, Any] = {
            "headless": self.headless,
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        }
        if settings.PLAYWRIGHT_CHROMIUM_EXECUTABLE:
            launch_kwargs["executable_path"] = settings.PLAYWRIGHT_CHROMIUM_EXECUTABLE
        proxy = ProxyManager.to_playwright(self.proxy_url)
        if proxy:
            launch_kwargs["proxy"] = proxy
        try:
            self.browser = self._pw.chromium.launch(**launch_kwargs)
        except Exception as exc:
            self._pw.stop()
            raise BrowserUnavailable(f"Could not launch Chromium: {exc}") from exc
        width, height = random.choice(VIEWPORTS)
        vendor, renderer = random.choice(WEBGL)
        context_kwargs: dict[str, Any] = {
            "user_agent": self.user_agent or random.choice(USER_AGENTS),
            "viewport": {"width": width, "height": height},
            "locale": "en-US",
            "timezone_id": self.timezone_id or "America/New_York",
            "java_script_enabled": True,
            "accept_downloads": False,
        }
        if self.storage_state:
            context_kwargs["storage_state"] = self.storage_state
        self.context = self.browser.new_context(**context_kwargs)
        self.context.set_default_timeout(settings.BROWSER_TIMEOUT_MS)
        self.context.add_init_script(
            STEALTH_JS % {"cores": random.choice([4, 8, 12, 16]), "vendor": vendor, "renderer": renderer}
        )
        if self.cookies:
            self.context.add_cookies(self.cookies)
        self.page = self.context.new_page()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        for closer in (
            lambda: self.context and self.context.close(),
            lambda: self.browser and self.browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass

    def screenshot(self, full_page: bool | None = None) -> bytes:
        return self.page.screenshot(full_page=settings.SCREENSHOT_FULL_PAGE if full_page is None else full_page, type="png")

    def html(self) -> str:
        return self.page.content()


def linkedin_cookies(li_at: str) -> list[dict[str, Any]]:
    return [
        {"name": "li_at", "value": li_at, "domain": ".linkedin.com", "path": "/", "httpOnly": True, "secure": True,
         "sameSite": "None"},
    ]
