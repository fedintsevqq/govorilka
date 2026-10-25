import pytest

from govorilka import updates, version

RELEASE = {
    "tag_name": "v0.3.3",
    "html_url": "https://github.com/fedintsevqq/govorilka/releases/tag/v0.3.3",
    "body": "Что нового\n" + "строка\n" * 200,
    "draft": False,
    "prerelease": False,
    "assets": [
        {"name": "Govorilka-0.3.3-windows-x64.exe", "browser_download_url": "https://example/win.exe", "size": 175_000_000, "digest": "sha256:aa11"},
        {"name": "Govorilka-0.3.3-macos-apple-silicon.dmg", "browser_download_url": "https://example/arm.dmg", "size": 157_000_000, "digest": "sha256:bb22"},
        {"name": "Govorilka-0.3.3-macos-intel.dmg", "browser_download_url": "https://example/intel.dmg", "size": 173_000_000, "digest": "sha256:cc33"},
    ],
}


def test_versions_are_compared_by_numbers_not_as_text():
    assert version.parse("v0.3.10") == (0, 3, 10) and version.parse("0.3.2") == (0, 3, 2)
    assert version.is_newer("0.3.10", "0.3.9") and not version.is_newer("0.3.9", "0.3.10")  # как числа, не как строки
    assert not version.is_newer("0.3.2", "0.3.2") and version.is_newer("1.0.0", "0.9.9")
    assert version.parse("latest") is None and not version.is_newer("мусор", "0.1.0")
    assert version.is_newer("0.0.1", "dev")  # из исходников любая версия новее


def test_the_right_file_is_picked_for_each_system():
    assert updates.asset_suffix("win32", "AMD64") == "windows-x64.exe"
    assert updates.asset_suffix("darwin", "arm64") == "macos-apple-silicon.dmg"
    assert updates.asset_suffix("darwin", "x86_64") == "macos-intel.dmg"
    for suffix, url, digest in (("windows-x64.exe", "https://example/win.exe", "aa11"), ("macos-intel.dmg", "https://example/intel.dmg", "cc33")):
        info = updates.parse_release(RELEASE, suffix)
        assert info.asset_url == url and info.sha256 == digest and info.version == "0.3.3"


def test_release_notes_are_cut_and_unfit_releases_are_ignored():
    info = updates.parse_release(RELEASE, "windows-x64.exe")
    assert len(info.notes) <= updates.NOTES_LIMIT + 1 and info.notes.endswith("…")
    assert updates.parse_release({**RELEASE, "prerelease": True}) is None
    assert updates.parse_release({**RELEASE, "draft": True}) is None
    assert updates.parse_release({**RELEASE, "tag_name": "nightly"}) is None


def test_a_release_without_a_file_for_this_system_is_still_announced():
    info = updates.parse_release({**RELEASE, "assets": []}, "windows-x64.exe")
    assert info.version == "0.3.3" and info.asset_url == "" and info.page_url.endswith("v0.3.3")


def test_check_reports_only_a_newer_version():
    assert updates.check("0.3.2", fetch=lambda: RELEASE, suffix="windows-x64.exe").version == "0.3.3"
    assert updates.check("0.3.3", fetch=lambda: RELEASE, suffix="windows-x64.exe") is None  # последняя
    assert updates.check("0.4.0", fetch=lambda: RELEASE, suffix="windows-x64.exe") is None  # у вас даже новее
    with pytest.raises(updates.UpdateError):
        updates.check("0.3.2", fetch=lambda: {"tag_name": "странное"})


def test_network_failures_become_update_errors(monkeypatch):
    import urllib.error

    def offline(*a, **k):
        raise OSError("нет сети")

    monkeypatch.setattr(updates.urllib.request, "urlopen", offline)
    with pytest.raises(updates.UpdateError, match="нет связи"):
        updates.fetch_latest()

    def refused(*a, **k):
        raise urllib.error.HTTPError("u", 403, "rate limit", {}, None)

    monkeypatch.setattr(updates.urllib.request, "urlopen", refused)
    with pytest.raises(updates.UpdateError, match="403"):
        updates.fetch_latest()


def test_the_real_github_answers_in_the_format_we_expect():
    """Живой запрос: если GitHub недоступен, тест пропускается, а не падает."""
    try:
        data = updates.fetch_latest(timeout=10)
    except updates.UpdateError as exc:
        pytest.skip(f"GitHub недоступен: {exc}")
    info = updates.parse_release(data, "windows-x64.exe")
    assert info is not None and version.parse(info.version) is not None
    assert info.asset_name.endswith("windows-x64.exe") and info.asset_url.startswith("https://github.com/")
    assert len(info.sha256) == 64 and info.size > 1_000_000
