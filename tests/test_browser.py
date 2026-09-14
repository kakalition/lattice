"""Browser tool unit tests (no Chromium required)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lattice.config import BrowserChannel, BrowserConfig
from lattice.deps import CORE_TOOL_NAMES
from lattice.tools.browser import (
    BROWSER_ACTIONS,
    _validate_url,
    browser_interact,
    browser_snapshot,
    channel_candidates,
    context_options,
    human_click,
    human_type,
    profile_dir_for,
    reset_driver,
)
from lattice.tools.groups import tool_functions


def test_context_options_look_human() -> None:
    from lattice.tools.browser import DEFAULT_USER_AGENT, LAUNCH_ARGS, STEALTH_INIT_SCRIPT

    opts = context_options(timezone_id="Asia/Jakarta", include_user_agent=True)
    assert opts["user_agent"] == DEFAULT_USER_AGENT
    assert "Headless" not in opts["user_agent"]
    assert opts["viewport"]["width"] >= 1024
    assert opts["locale"] == "en-US"
    assert opts["timezone_id"] == "Asia/Jakarta"
    assert "AutomationControlled" in " ".join(LAUNCH_ARGS)
    assert "webdriver" in STEALTH_INIT_SCRIPT

    chrome_opts = context_options(include_user_agent=False)
    assert "user_agent" not in chrome_opts


def test_channel_candidates_auto_prefers_chrome() -> None:
    assert channel_candidates(BrowserConfig(channel=BrowserChannel.AUTO)) == ["chrome", None]
    assert channel_candidates(BrowserConfig(channel=BrowserChannel.CHROME)) == ["chrome"]
    assert channel_candidates(BrowserConfig(channel=BrowserChannel.CHROMIUM)) == [None]


def test_profile_dir_default_and_override(tmp_path: Path) -> None:
    cfg = BrowserConfig()
    assert profile_dir_for(cfg, tmp_path) == (tmp_path / "browser" / "profile").resolve()
    cfg2 = BrowserConfig(profile_dir="custom/profile")
    assert profile_dir_for(cfg2, tmp_path) == (tmp_path / "custom" / "profile").resolve()
    abs_dir = tmp_path / "abs"
    cfg3 = BrowserConfig(profile_dir=str(abs_dir))
    assert profile_dir_for(cfg3, tmp_path) == abs_dir.resolve()


def test_browser_config_in_settings() -> None:
    from lattice.config import LatticeSettings

    s = LatticeSettings(home=Path("/tmp/lattice-test-home"))
    assert s.browser.channel == BrowserChannel.AUTO
    assert s.browser.persistent_profile is True
    assert s.browser.humanize is True
    assert s.browser.headed is False


def test_core_tools_include_browser() -> None:
    assert "browser/interact" in CORE_TOOL_NAMES
    assert "browser/snapshot" in CORE_TOOL_NAMES


def test_build_toolsets_includes_browser_tools() -> None:

    mapping = tool_functions()
    assert "browser/interact" in mapping
    assert "browser/snapshot" in mapping
    assert set(CORE_TOOL_NAMES) <= set(mapping)


def test_validate_url_blocks_dangerous_schemes() -> None:
    assert _validate_url("https://example.com") is None
    assert _validate_url("http://example.com/path") is None
    assert _validate_url("file:///etc/passwd") is not None
    assert _validate_url("javascript:alert(1)") is not None
    assert _validate_url("ftp://example.com") is not None
    assert _validate_url("") is not None


@pytest.mark.asyncio
async def test_browser_interact_rejects_unknown_action() -> None:
    out = await browser_interact("hover")
    assert "unknown action" in out
    for action in sorted(BROWSER_ACTIONS):
        assert action in out or action == "close"


@pytest.mark.asyncio
async def test_browser_interact_navigate_requires_http() -> None:
    out = await browser_interact("navigate", url="file:///tmp/x")
    assert "blocked" in out or "scheme" in out


@pytest.mark.asyncio
async def test_browser_snapshot_unknown_mode() -> None:
    out = await browser_snapshot(mode="png")
    assert "unknown mode" in out


@pytest.mark.asyncio
async def test_browser_missing_or_unavailable_is_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    await reset_driver()

    async def _boom() -> None:
        from lattice.tools.browser import BrowserUnavailable

        raise BrowserUnavailable("browser unavailable: test")

    class FakeDriver:
        async def ensure(self) -> None:
            await _boom()

        page = None

    async def fake_get() -> FakeDriver:
        return FakeDriver()

    monkeypatch.setattr("lattice.tools.browser.get_driver", fake_get)
    out = await browser_snapshot()
    assert "browser unavailable" in out
    out2 = await browser_interact("click", selector="#x")
    assert "browser unavailable" in out2


@pytest.mark.asyncio
async def test_human_click_moves_mouse_when_enabled() -> None:
    page = MagicMock()
    loc = MagicMock()
    loc.wait_for = AsyncMock()
    loc.bounding_box = AsyncMock(return_value={"x": 100, "y": 50, "width": 40, "height": 20})
    loc.click = AsyncMock()
    page.locator.return_value.first = loc
    page.mouse.move = AsyncMock()

    await human_click(page, "#go", timeout_ms=1000, humanize=True)
    assert page.mouse.move.await_count >= 2
    loc.click.assert_awaited()


@pytest.mark.asyncio
async def test_human_type_uses_press_sequentially() -> None:
    page = MagicMock()
    loc = MagicMock()
    loc.wait_for = AsyncMock()
    loc.bounding_box = AsyncMock(return_value={"x": 10, "y": 10, "width": 80, "height": 20})
    loc.click = AsyncMock()
    loc.fill = AsyncMock()
    loc.press_sequentially = AsyncMock()
    page.locator.return_value.first = loc
    page.mouse.move = AsyncMock()

    await human_type(
        page,
        "#email",
        "a@b.co",
        timeout_ms=1000,
        humanize=True,
        delay_ms_min=10,
        delay_ms_max=20,
    )
    loc.fill.assert_awaited()
    loc.press_sequentially.assert_awaited()
