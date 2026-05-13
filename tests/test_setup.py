from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from linkedin_mcp_server.config.schema import AppConfig
from linkedin_mcp_server.session_state import portable_cookie_path
from linkedin_mcp_server.setup import interactive_login


class _BrowserContextManager:
    def __init__(self, browser):
        self.browser = browser

    async def __aenter__(self):
        return self.browser

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _make_browser(*, export_cookies: bool) -> MagicMock:
    browser = MagicMock()
    browser.page = MagicMock()
    browser.page.goto = AsyncMock()
    browser.context = MagicMock()
    browser.context.cookies = AsyncMock(
        return_value=[{"name": "li_at", "domain": ".linkedin.com"}]
    )
    browser.export_cookies = AsyncMock(return_value=export_cookies)
    return browser


def _patch_login_deps(
    monkeypatch,
    *,
    browser_factory,
    config: AppConfig | None = None,
    write_source_state: MagicMock | None = None,
) -> None:
    """Patch all interactive_login dependencies in one place."""
    resolved_config = config or AppConfig()
    monkeypatch.setattr(
        "linkedin_mcp_server.setup.get_config", lambda: resolved_config
    )
    # The browser-launch helper now lives in drivers.browser; patch its
    # get_config so the precedence chain (chrome_path > browser_channel > ...)
    # sees the test config too.
    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser.get_config", lambda: resolved_config
    )
    monkeypatch.setattr("linkedin_mcp_server.setup.BrowserManager", browser_factory)
    monkeypatch.setattr("linkedin_mcp_server.setup.warm_up_browser", AsyncMock())
    monkeypatch.setattr(
        "linkedin_mcp_server.setup.resolve_remember_me_prompt",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr("linkedin_mcp_server.setup.wait_for_manual_login", AsyncMock())
    monkeypatch.setattr(
        "linkedin_mcp_server.setup.write_source_state",
        write_source_state
        or MagicMock(return_value=SimpleNamespace(login_generation="gen-1")),
    )
    monkeypatch.setattr("linkedin_mcp_server.setup.asyncio.sleep", AsyncMock())


@pytest.mark.asyncio
async def test_interactive_login_writes_source_state_when_cookie_export_succeeds(
    monkeypatch, tmp_path, capsys
):
    browser = _make_browser(export_cookies=True)
    write_source_state = MagicMock(
        return_value=SimpleNamespace(login_generation="gen-123")
    )

    _patch_login_deps(
        monkeypatch,
        browser_factory=lambda **kwargs: _BrowserContextManager(browser),
        write_source_state=write_source_state,
    )

    assert await interactive_login(tmp_path / "profile") is True

    browser.export_cookies.assert_awaited_once_with(
        portable_cookie_path(tmp_path / "profile")
    )
    write_source_state.assert_called_once_with(tmp_path / "profile")
    captured = capsys.readouterr()
    assert "cookies exported for docker portability" in captured.out.lower()
    assert "source session generation: gen-123" in captured.out.lower()


@pytest.mark.asyncio
async def test_interactive_login_returns_false_when_cookie_export_fails(
    monkeypatch, tmp_path, capsys
):
    browser = _make_browser(export_cookies=False)
    write_source_state = MagicMock()

    _patch_login_deps(
        monkeypatch,
        browser_factory=lambda **kwargs: _BrowserContextManager(browser),
        write_source_state=write_source_state,
    )

    assert await interactive_login(tmp_path / "profile") is False

    browser.export_cookies.assert_awaited_once_with(
        portable_cookie_path(tmp_path / "profile")
    )
    write_source_state.assert_not_called()
    captured = capsys.readouterr()
    assert "warning: cookie export failed" in captured.out.lower()
    assert "profile saved to" not in captured.out.lower()


@pytest.mark.asyncio
async def test_interactive_login_passes_chrome_path_to_browser_manager(
    monkeypatch, tmp_path
):
    """When config.browser.chrome_path is set, executable_path must reach BrowserManager."""
    browser = _make_browser(export_cookies=True)
    captured_kwargs: dict = {}

    def fake_browser_manager(**kwargs):
        captured_kwargs.update(kwargs)
        return _BrowserContextManager(browser)

    config = AppConfig()
    config.browser.chrome_path = "/custom/chrome"

    _patch_login_deps(monkeypatch, browser_factory=fake_browser_manager, config=config)

    await interactive_login(tmp_path / "profile")

    assert captured_kwargs.get("executable_path") == "/custom/chrome"


@pytest.mark.asyncio
async def test_interactive_login_forwards_all_browser_params(monkeypatch, tmp_path):
    """All browser config params must reach BrowserManager during --login."""
    browser = _make_browser(export_cookies=True)
    captured_kwargs: dict = {}

    def fake_browser_manager(**kwargs):
        captured_kwargs.update(kwargs)
        return _BrowserContextManager(browser)

    config = AppConfig()
    config.browser.chrome_path = "/custom/chrome"
    config.browser.slow_mo = 250
    config.browser.user_agent = "CustomAgent/1.0"
    config.browser.viewport_width = 1920
    config.browser.viewport_height = 1080

    _patch_login_deps(monkeypatch, browser_factory=fake_browser_manager, config=config)

    profile = tmp_path / "profile"
    await interactive_login(profile)

    assert captured_kwargs["user_data_dir"] == profile
    assert captured_kwargs["headless"] is False
    assert captured_kwargs["slow_mo"] == 250
    assert captured_kwargs["user_agent"] == "CustomAgent/1.0"
    assert captured_kwargs["viewport"] == {"width": 1920, "height": 1080}
    assert captured_kwargs["executable_path"] == "/custom/chrome"


@pytest.mark.asyncio
async def test_interactive_login_passes_slow_mo_to_browser_manager(
    monkeypatch, tmp_path
):
    """When config.browser.slow_mo is set, it must reach BrowserManager."""
    browser = _make_browser(export_cookies=True)
    captured_kwargs: dict = {}

    def fake_browser_manager(**kwargs):
        captured_kwargs.update(kwargs)
        return _BrowserContextManager(browser)

    config = AppConfig()
    config.browser.slow_mo = 250

    _patch_login_deps(monkeypatch, browser_factory=fake_browser_manager, config=config)

    await interactive_login(tmp_path / "profile")

    assert captured_kwargs.get("slow_mo") == 250


@pytest.mark.asyncio
async def test_interactive_login_passes_user_agent_to_browser_manager(
    monkeypatch, tmp_path
):
    """When config.browser.user_agent is set, it must reach BrowserManager."""
    browser = _make_browser(export_cookies=True)
    captured_kwargs: dict = {}

    def fake_browser_manager(**kwargs):
        captured_kwargs.update(kwargs)
        return _BrowserContextManager(browser)

    config = AppConfig()
    config.browser.user_agent = "CustomAgent/1.0"

    _patch_login_deps(monkeypatch, browser_factory=fake_browser_manager, config=config)

    await interactive_login(tmp_path / "profile")

    assert captured_kwargs.get("user_agent") == "CustomAgent/1.0"


@pytest.mark.asyncio
async def test_interactive_login_passes_viewport_to_browser_manager(
    monkeypatch, tmp_path
):
    """Non-default viewport_width/viewport_height must reach BrowserManager as viewport."""
    browser = _make_browser(export_cookies=True)
    captured_kwargs: dict = {}

    def fake_browser_manager(**kwargs):
        captured_kwargs.update(kwargs)
        return _BrowserContextManager(browser)

    config = AppConfig()
    config.browser.viewport_width = 1920
    config.browser.viewport_height = 1080

    _patch_login_deps(monkeypatch, browser_factory=fake_browser_manager, config=config)

    await interactive_login(tmp_path / "profile")

    assert captured_kwargs.get("viewport") == {"width": 1920, "height": 1080}


@pytest.mark.asyncio
async def test_interactive_login_passes_browser_channel_to_browser_manager(
    monkeypatch, tmp_path
):
    """When config.browser.browser_channel is set and chrome_path is not,
    Patchright's channel keyword must reach BrowserManager."""
    browser = _make_browser(export_cookies=True)
    captured_kwargs: dict = {}

    def fake_browser_manager(**kwargs):
        captured_kwargs.update(kwargs)
        return _BrowserContextManager(browser)

    config = AppConfig()
    config.browser.browser_channel = "chromium"

    _patch_login_deps(monkeypatch, browser_factory=fake_browser_manager, config=config)

    await interactive_login(tmp_path / "profile")

    assert captured_kwargs.get("channel") == "chromium"
    assert "executable_path" not in captured_kwargs


@pytest.mark.asyncio
async def test_interactive_login_chrome_path_wins_over_browser_channel(
    monkeypatch, tmp_path
):
    """chrome_path must take precedence over browser_channel when both are set."""
    browser = _make_browser(export_cookies=True)
    captured_kwargs: dict = {}

    def fake_browser_manager(**kwargs):
        captured_kwargs.update(kwargs)
        return _BrowserContextManager(browser)

    config = AppConfig()
    config.browser.chrome_path = "/custom/chrome"
    config.browser.browser_channel = "chrome"  # should be ignored

    _patch_login_deps(monkeypatch, browser_factory=fake_browser_manager, config=config)

    await interactive_login(tmp_path / "profile")

    assert captured_kwargs.get("executable_path") == "/custom/chrome"
    assert "channel" not in captured_kwargs


@pytest.mark.asyncio
async def test_interactive_login_macos_falls_back_to_bundled_chromium(
    monkeypatch, tmp_path
):
    """On macOS with no overrides, the bundled Patchright Chromium must be used
    via executable_path (preserves anti-detection)."""
    browser = _make_browser(export_cookies=True)
    captured_kwargs: dict = {}

    def fake_browser_manager(**kwargs):
        captured_kwargs.update(kwargs)
        return _BrowserContextManager(browser)

    fake_chromium = tmp_path / "fake-chromium"
    fake_chromium.write_text("")

    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser.platform.system",
        lambda: "Darwin",
    )
    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser._find_patchright_chromium",
        lambda: fake_chromium,
    )

    config = AppConfig()  # no chrome_path, no browser_channel

    _patch_login_deps(monkeypatch, browser_factory=fake_browser_manager, config=config)

    await interactive_login(tmp_path / "profile")

    assert captured_kwargs.get("executable_path") == str(fake_chromium)
    assert "channel" not in captured_kwargs


@pytest.mark.asyncio
async def test_interactive_login_macos_falls_back_to_system_chrome_when_bundled_missing(
    monkeypatch, tmp_path
):
    """On macOS with no bundled Chromium but system Chrome installed, fall back
    to channel='chrome' with a warning."""
    browser = _make_browser(export_cookies=True)
    captured_kwargs: dict = {}

    def fake_browser_manager(**kwargs):
        captured_kwargs.update(kwargs)
        return _BrowserContextManager(browser)

    fake_chrome = tmp_path / "Google Chrome"
    fake_chrome.write_text("")

    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser.platform.system",
        lambda: "Darwin",
    )
    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser._find_patchright_chromium",
        lambda: None,
    )
    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser._CHROME_APP_MACOS",
        fake_chrome,
    )

    config = AppConfig()

    _patch_login_deps(monkeypatch, browser_factory=fake_browser_manager, config=config)

    await interactive_login(tmp_path / "profile")

    assert captured_kwargs.get("channel") == "chrome"


@pytest.mark.asyncio
async def test_interactive_login_macos_raises_when_no_browser_available(
    monkeypatch, tmp_path
):
    """On macOS with neither bundled Chromium nor system Chrome, the helper
    must raise BrowserSetupFailedError with a remediation hint."""
    from linkedin_mcp_server.exceptions import BrowserSetupFailedError

    browser = _make_browser(export_cookies=True)

    def fake_browser_manager(**kwargs):
        return _BrowserContextManager(browser)

    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser.platform.system",
        lambda: "Darwin",
    )
    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser._find_patchright_chromium",
        lambda: None,
    )
    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser._CHROME_APP_MACOS",
        tmp_path / "nonexistent",
    )

    config = AppConfig()

    _patch_login_deps(monkeypatch, browser_factory=fake_browser_manager, config=config)

    with pytest.raises(BrowserSetupFailedError) as exc_info:
        await interactive_login(tmp_path / "profile")
    assert "patchright install chromium" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_interactive_login_non_darwin_uses_patchright_default(
    monkeypatch, tmp_path
):
    """On Linux/Windows with no overrides, launch_options must be empty so
    Patchright uses its default bundled Chromium (unchanged upstream behavior)."""
    browser = _make_browser(export_cookies=True)
    captured_kwargs: dict = {}

    def fake_browser_manager(**kwargs):
        captured_kwargs.update(kwargs)
        return _BrowserContextManager(browser)

    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser.platform.system",
        lambda: "Linux",
    )

    config = AppConfig()

    _patch_login_deps(monkeypatch, browser_factory=fake_browser_manager, config=config)

    await interactive_login(tmp_path / "profile")

    assert "channel" not in captured_kwargs
    assert "executable_path" not in captured_kwargs
