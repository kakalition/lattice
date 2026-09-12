"""Headless/headed Chromium browser (Playwright) for SPA / interactive pages.

Provides Puppeteer-style actions: navigate, click, type, select, extract_text.
``web_fetch`` remains for static HTML; use this when the page needs DOM execution.

Defaults prefer system Chrome + a persistent profile under ``.lattice/browser/profile``,
with optional headed mode and humanized click/type (see ``browser:`` in lattice.yaml).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path
from typing import Any

from lattice.config import BrowserChannel, BrowserConfig, load_settings
from lattice.paths import lattice_home
from lattice.tools.web import fence_untrusted

logger = logging.getLogger("lattice.browser")

# Fallback UA only when forcing Chromium without a real Chrome profile.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_VIEWPORT = {"width": 1280, "height": 800}

STEALTH_INIT_SCRIPT = """
(() => {
  try {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  } catch (e) {}
  try {
    window.chrome = window.chrome || { runtime: {} };
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'languages', {
      get: () => ['en-US', 'en'],
    });
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'plugins', {
      get: () => [1, 2, 3, 4, 5],
    });
  } catch (e) {}
  try {
    const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
    if (originalQuery) {
      window.navigator.permissions.query = (parameters) => (
        parameters && parameters.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission })
          : originalQuery(parameters)
      );
    }
  } catch (e) {}
})();
"""

LAUNCH_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
)

BROWSER_ACTIONS = frozenset({"navigate", "click", "type", "select", "extract_text", "close"})
SNAPSHOT_MODES = frozenset({"a11y", "text"})


def _truncate(text: str, limit: int = 30_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def _browser_timezone() -> str:
    try:
        from lattice.timeutil import resolve_timezone

        return resolve_timezone() or "UTC"
    except Exception:
        return "UTC"


def profile_dir_for(cfg: BrowserConfig, home: Path | None = None) -> Path:
    root = home or lattice_home()
    if cfg.profile_dir:
        p = Path(cfg.profile_dir).expanduser()
        return p if p.is_absolute() else (root / p).resolve()
    return (root / "browser" / "profile").resolve()


def channel_candidates(cfg: BrowserConfig) -> list[str | None]:
    """Ordered Playwright ``channel`` values to try (None = bundled Chromium)."""
    if cfg.channel == BrowserChannel.CHROME:
        return ["chrome"]
    if cfg.channel == BrowserChannel.CHROMIUM:
        return [None]
    # auto
    return ["chrome", None]


def context_options(
    *,
    timezone_id: str | None = None,
    include_user_agent: bool = True,
) -> dict[str, Any]:
    """Realistic browser context defaults.

    When using system Chrome + persistent profile, omit user_agent so Chrome's
    real UA is used (forced UA mismatches are a bot signal).
    """
    opts: dict[str, Any] = {
        "viewport": dict(DEFAULT_VIEWPORT),
        "locale": "en-US",
        "timezone_id": timezone_id or _browser_timezone(),
        "color_scheme": "light",
        "java_script_enabled": True,
        "has_touch": False,
        "is_mobile": False,
        "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
    }
    if include_user_agent:
        opts["user_agent"] = DEFAULT_USER_AGENT
    return opts


def load_browser_config() -> BrowserConfig:
    try:
        return load_settings().browser
    except Exception:
        return BrowserConfig()


class BrowserUnavailable(Exception):
    pass


async def _human_pause(lo: float = 0.05, hi: float = 0.2) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


async def human_click(page: Any, selector: str, *, timeout_ms: int, humanize: bool) -> None:
    loc = page.locator(selector).first
    await loc.wait_for(state="visible", timeout=timeout_ms)
    if humanize:
        box = await loc.bounding_box()
        if box:
            tx = box["x"] + box["width"] * random.uniform(0.35, 0.65)
            ty = box["y"] + box["height"] * random.uniform(0.35, 0.65)
            # Approach from a nearby point, then click.
            await page.mouse.move(tx + random.uniform(-40, 40), ty + random.uniform(-30, 30))
            await _human_pause(0.04, 0.12)
            await page.mouse.move(tx, ty)
            await _human_pause(0.03, 0.1)
        await loc.click(timeout=timeout_ms, delay=random.randint(20, 80))
        await _human_pause(0.08, 0.28)
    else:
        await loc.click(timeout=timeout_ms)


async def human_type(
    page: Any,
    selector: str,
    value: str,
    *,
    timeout_ms: int,
    humanize: bool,
    delay_ms_min: int,
    delay_ms_max: int,
) -> None:
    loc = page.locator(selector).first
    await loc.wait_for(state="visible", timeout=timeout_ms)
    if humanize:
        await human_click(page, selector, timeout_ms=timeout_ms, humanize=True)
        await loc.fill("")
        delay = random.randint(max(1, delay_ms_min), max(delay_ms_min, delay_ms_max))
        await loc.press_sequentially(value, delay=delay)
        await _human_pause(0.08, 0.25)
    else:
        await loc.fill(value, timeout=timeout_ms)


class BrowserDriver:
    """Process-scoped page shared across tool calls."""

    def __init__(self, cfg: BrowserConfig | None = None) -> None:
        self.cfg = cfg or load_browser_config()
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self.page: Any = None
        self.backend: str = ""

    async def ensure(self) -> None:
        if self.page is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserUnavailable(
                "browser unavailable: install playwright (`uv add playwright`); "
                "Chromium auto-installs on `lattice chat` / `lattice gateway`"
            ) from exc

        self.cfg = load_browser_config()
        home = lattice_home()
        profile = profile_dir_for(self.cfg, home)
        profile.mkdir(parents=True, exist_ok=True)

        errors: list[str] = []
        self._pw = await async_playwright().start()
        for channel in channel_candidates(self.cfg):
            label = channel or "chromium"
            try:
                await self._launch(channel=channel, profile=profile)
                self.backend = label
                logger.info(
                    "browser ready backend=%s headed=%s persistent=%s profile=%s",
                    label,
                    self.cfg.headed,
                    self.cfg.persistent_profile,
                    profile if self.cfg.persistent_profile else "(ephemeral)",
                )
                return
            except Exception as exc:
                errors.append(f"{label}: {exc}")
                await self._cleanup_partial()
                continue

        await self.close()
        detail = "; ".join(errors) or "unknown"
        if any("Executable doesn't exist" in e for e in errors):
            raise BrowserUnavailable(
                "browser unavailable: no Chrome/Chromium — install Google Chrome "
                "or re-run chat/gateway to install Playwright Chromium. "
                f"Tried: {detail}"
            )
        raise BrowserUnavailable(f"browser unavailable: {detail}")

    async def _launch(self, *, channel: str | None, profile: Path) -> None:
        assert self._pw is not None
        headless = not self.cfg.headed
        use_chrome = channel == "chrome"
        # Real Chrome profile: don't override UA. Bundled Chromium: set a desktop UA.
        ctx_opts = context_options(include_user_agent=not use_chrome)
        launch_kwargs: dict[str, Any] = {
            "headless": headless,
            "args": list(LAUNCH_ARGS),
            "ignore_default_args": ["--enable-automation"],
        }
        if channel:
            launch_kwargs["channel"] = channel

        if self.cfg.persistent_profile:
            self._context = await self._pw.chromium.launch_persistent_context(
                str(profile),
                **launch_kwargs,
                **ctx_opts,
            )
            await self._context.add_init_script(STEALTH_INIT_SCRIPT)
            self.page = (
                self._context.pages[0] if self._context.pages else await self._context.new_page()
            )
        else:
            self._browser = await self._pw.chromium.launch(**launch_kwargs)
            self._context = await self._browser.new_context(**ctx_opts)
            await self._context.add_init_script(STEALTH_INIT_SCRIPT)
            self.page = await self._context.new_page()

    async def _cleanup_partial(self) -> None:
        import contextlib

        for obj in (self._context, self._browser):
            if obj is None:
                continue
            with contextlib.suppress(Exception):
                await obj.close()
        self._context = None
        self._browser = None
        self.page = None

    async def close(self) -> None:
        import contextlib

        await self._cleanup_partial()
        if self._pw is not None:
            with contextlib.suppress(Exception):
                await self._pw.stop()
        self._pw = None
        self.backend = ""


_LOCK = asyncio.Lock()
_DRIVER: BrowserDriver | None = None


async def get_driver() -> BrowserDriver:
    global _DRIVER
    async with _LOCK:
        if _DRIVER is None:
            _DRIVER = BrowserDriver()
        return _DRIVER


async def reset_driver() -> None:
    """Close and drop the process browser (tests / shutdown)."""
    global _DRIVER
    async with _LOCK:
        if _DRIVER is not None:
            await _DRIVER.close()
            _DRIVER = None


def _validate_url(url: str) -> str | None:
    u = url.strip()
    if not u:
        return "navigate requires url"
    lower = u.lower()
    if lower.startswith(("file:", "javascript:", "data:")):
        return f"blocked url scheme: {u.split(':', 1)[0]}"
    if not (lower.startswith("http://") or lower.startswith("https://")):
        return "navigate url must be http(s)"
    return None


def _format_ax_node(node: dict[str, Any] | None, *, indent: int = 0) -> list[str]:
    if not node:
        return []
    role = node.get("role") or "unknown"
    name = (node.get("name") or "").strip()
    value = node.get("value")
    bits = [role]
    if name:
        bits.append(f'"{name}"')
    if value not in (None, ""):
        bits.append(f"value={value!r}")
    for key in ("description", "checked", "selected", "disabled", "focused"):
        if key in node and node[key] not in (None, False, ""):
            bits.append(f"{key}={node[key]!r}")
    lines = ["  " * indent + " ".join(bits)]
    for child in node.get("children") or []:
        if isinstance(child, dict):
            lines.extend(_format_ax_node(child, indent=indent + 1))
    return lines


async def browser_snapshot(*, mode: str = "a11y", max_chars: int = 30_000) -> str:
    mode = (mode or "a11y").strip().lower()
    if mode not in SNAPSHOT_MODES:
        return f"error: unknown mode {mode!r}; use a11y or text"
    try:
        driver = await get_driver()
        await driver.ensure()
    except BrowserUnavailable as exc:
        return f"error: {exc}"
    page = driver.page
    assert page is not None
    url = page.url or "(about:blank)"
    try:
        if mode == "text":
            body = await page.inner_text("body")
            label = f"browser-text:{url}"
        else:
            body = ""
            try:
                body = await page.locator("body").aria_snapshot()
            except Exception:
                ax = await page.accessibility.snapshot()
                body = "\n".join(_format_ax_node(ax)) if isinstance(ax, dict) else str(ax or "")
            label = f"browser-a11y:{url}"
    except Exception as exc:
        return f"browser_snapshot error: {exc}"
    return fence_untrusted(label, _truncate((body or "").strip() or "(empty)", max_chars))


async def browser_interact(
    action: str,
    *,
    selector: str | None = None,
    value: str | None = None,
    url: str | None = None,
    timeout_ms: int = 15_000,
) -> str:
    action = (action or "").strip().lower()
    if action not in BROWSER_ACTIONS:
        return f"error: unknown action {action!r}; use one of: {', '.join(sorted(BROWSER_ACTIONS))}"

    if action == "close":
        await reset_driver()
        return "browser closed"

    if action == "navigate":
        err = _validate_url(url or "")
        if err:
            return f"error: {err}"
    elif action in {"click", "type", "select"} and not selector:
        return f"error: {action} requires selector"
    elif action == "type" and value is None:
        return "error: type requires value"
    elif action == "select" and value is None:
        return "error: select requires value"

    try:
        driver = await get_driver()
        await driver.ensure()
    except BrowserUnavailable as exc:
        return f"error: {exc}"
    page = driver.page
    assert page is not None
    cfg = driver.cfg

    try:
        if action == "navigate":
            assert url is not None
            resp = await page.goto(url.strip(), wait_until="domcontentloaded", timeout=timeout_ms)
            if cfg.humanize:
                await _human_pause(0.15, 0.45)
            status = resp.status if resp else "?"
            title = await page.title()
            return (
                f"navigated status={status} title={title!r} url={page.url} backend={driver.backend}"
            )

        if action == "click":
            assert selector is not None
            await human_click(page, selector, timeout_ms=timeout_ms, humanize=cfg.humanize)
            return f"clicked {selector!r} url={page.url}"

        if action == "type":
            assert selector is not None and value is not None
            await human_type(
                page,
                selector,
                value,
                timeout_ms=timeout_ms,
                humanize=cfg.humanize,
                delay_ms_min=cfg.type_delay_ms_min,
                delay_ms_max=cfg.type_delay_ms_max,
            )
            return f"typed into {selector!r} ({len(value)} chars) url={page.url}"

        if action == "select":
            assert selector is not None and value is not None
            if cfg.humanize:
                await human_click(page, selector, timeout_ms=timeout_ms, humanize=True)
            values: list[str]
            raw = value.strip()
            if raw.startswith("["):
                parsed = json.loads(raw)
                if not isinstance(parsed, list):
                    return "select value JSON must be a list of strings"
                values = [str(v) for v in parsed]
            else:
                values = [value]
            selected = await page.select_option(selector, values, timeout=timeout_ms)
            if cfg.humanize:
                await _human_pause(0.08, 0.25)
            return f"selected {selected!r} on {selector!r} url={page.url}"

        if action == "extract_text":
            sel = selector or "body"
            text = await page.inner_text(sel, timeout=timeout_ms)
            return fence_untrusted(
                f"browser-extract:{page.url}:{sel}",
                _truncate((text or "").strip() or "(empty)"),
            )

    except Exception as exc:
        return f"browser_interact error ({action}): {exc}"

    return f"unhandled action {action!r}"
