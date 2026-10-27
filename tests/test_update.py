import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app.bundle_path import original_translocated_path
from app.update import (
    MACOS_HELPER,
    WINDOWS_HELPER,
    UpdateError,
    UpdateOffer,
    assert_zip_safe,
    check_url,
    current_version,
    extract_archive,
    extract_zip,
    find_payload,
    install_target,
    normalize_version,
    offer_from_release,
    parse_version,
    platform_tag,
    updates_supported,
    validate_swap,
)


ASSET = "https://github.com/ijustseen/openTwitchBot/releases/download/v0.2.0/OpenBot-macos-arm64.zip"


def release(*names: str) -> dict:
    assets = []
    for name in names:
        assets.append(
            {
                "name": name,
                "browser_download_url": f"https://github.com/ijustseen/openTwitchBot/releases/download/v0.2.0/{name}",
                "size": 1000,
            }
        )
    return {"tag_name": "v0.2.0", "assets": assets}


def test_parse_version() -> None:
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version(" 0.1.1 ") == (0, 1, 1)
    assert parse_version("1.2") is None
    assert parse_version("main") is None
    assert parse_version("1.2.3-beta") is None
    assert normalize_version("v0.1.1") == "0.1.1"
    assert normalize_version("main") == "0.0.0"


def test_platform_tag() -> None:
    assert platform_tag("darwin", "arm64") == "macos-arm64"
    assert platform_tag("win32", "AMD64") == "windows-x64"


def test_offer_picks_asset_for_this_system() -> None:
    payload = release("OpenBot-macos-arm64.zip", "OpenBot-windows-x64.zip", "notes.txt")
    offer = offer_from_release(payload, current="0.1.1", platform_name="darwin", machine="arm64")
    assert offer == UpdateOffer("0.2.0", "OpenBot-macos-arm64.zip", ASSET, 1000)
    assert offer_from_release(payload, current="0.2.0", platform_name="darwin", machine="arm64") is None
    assert offer_from_release(payload, current="1.0.0", platform_name="darwin", machine="arm64") is None


def test_offer_rejects_foreign_url_and_missing_build() -> None:
    payload = release("OpenBot-macos-arm64.zip")
    payload["assets"][0]["browser_download_url"] = "https://example.com/OpenBot-macos-arm64.zip"
    with pytest.raises(UpdateError):
        offer_from_release(payload, current="0.1.0", platform_name="darwin", machine="arm64")
    with pytest.raises(UpdateError):
        offer_from_release(release("OpenBot-windows-x64.zip"), current="0.1.0", platform_name="darwin", machine="arm64")
    with pytest.raises(UpdateError):
        check_url("http://github.com/ijustseen/openTwitchBot/releases/download/v0.2.0/OpenBot.zip")
    with pytest.raises(UpdateError):
        check_url("https://user@github.com/ijustseen/openTwitchBot/releases/download/v0.2.0/a.zip")


def test_current_version_and_support(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.update.resource_root", lambda: tmp_path)
    monkeypatch.setattr("app.update.install_target", lambda: tmp_path)
    assert current_version() == "0.0.0"
    assert updates_supported() is False
    (tmp_path / "version.txt").write_text("v0.3.1\n", encoding="utf-8")
    assert current_version() == "0.3.1"
    assert updates_supported() is True


def test_plain_path_is_not_translocated(tmp_path: Path) -> None:
    if sys.platform != "darwin":
        pytest.skip("только macOS")
    marker = tmp_path / "plain.txt"
    marker.write_text("x", encoding="utf-8")
    assert original_translocated_path(marker) is None


def test_translocated_bundle_uses_the_real_app(tmp_path: Path, monkeypatch) -> None:
    real = tmp_path / "Downloads" / "OpenBot.app" / "Contents" / "MacOS" / "OpenBot"
    real.parent.mkdir(parents=True)
    real.write_text("x", encoding="utf-8")
    moved = tmp_path / "AppTranslocation" / "id" / "d" / "OpenBot.app" / "Contents" / "MacOS" / "OpenBot"
    moved.parent.mkdir(parents=True)
    moved.write_text("x", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(moved))
    monkeypatch.setattr("app.bundle_path.original_translocated_path", lambda _path: real)
    assert install_target() == real.parents[2]
    from app.store import user_root

    assert user_root() == tmp_path / "Downloads"


def test_install_target_follows_frozen_layout(tmp_path: Path, monkeypatch) -> None:
    app = tmp_path / "OpenBot.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    exe = app / "OpenBot"
    exe.write_text("x", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert install_target() == tmp_path / "OpenBot.app"

    folder = tmp_path / "Dist"
    folder.mkdir()
    win_exe = folder / "OpenBot.exe"
    win_exe.write_text("x", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(win_exe))
    monkeypatch.setattr(sys, "platform", "win32")
    assert install_target() == folder


def test_zip_slip_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../evil.txt", "no")
    with pytest.raises(UpdateError):
        assert_zip_safe(archive)


def test_extract_zip_finds_windows_folder(tmp_path: Path) -> None:
    archive = tmp_path / "OpenBot-windows-x64.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("OpenBot/OpenBot.exe", b"MZ")
    dest = tmp_path / "out"
    extract_zip(archive, dest)
    assert find_payload(dest, platform_name="win32") == dest / "OpenBot"


def test_swap_refuses_to_touch_the_directory_it_cleans(tmp_path: Path) -> None:
    cleanup = tmp_path / "tmp"
    source = cleanup / "extracted" / "OpenBot.app"
    source.mkdir(parents=True)
    target = tmp_path / "Apps" / "OpenBot.app"
    target.mkdir(parents=True)
    validate_swap(target, source, cleanup)
    with pytest.raises(UpdateError):
        validate_swap(cleanup, source, cleanup)


def test_helpers_keep_user_data() -> None:
    assert 'mv "$target" "$backup"' in MACOS_HELPER
    assert 'open "$target"' in MACOS_HELPER
    assert "Join-Path $Target \"data\"" in WINDOWS_HELPER
    assert "OpenBot.exe" in WINDOWS_HELPER


@pytest.mark.skipif(sys.platform != "darwin", reason="нужен ditto")
def test_macos_archive_keeps_symlinks(tmp_path: Path) -> None:
    app = tmp_path / "OpenBot.app" / "Contents"
    resources = app / "Resources"
    frameworks = app / "Frameworks"
    resources.mkdir(parents=True)
    frameworks.mkdir()
    (resources / "marker.txt").write_text("ok", encoding="utf-8")
    (frameworks / "marker.txt").symlink_to("../Resources/marker.txt")
    macos = app / "MacOS"
    macos.mkdir()
    exe = macos / "OpenBot"
    exe.write_text("bin", encoding="utf-8")
    archive = tmp_path / "OpenBot-macos-arm64.zip"
    subprocess.run(
        ["ditto", "-c", "-k", "--norsrc", "--keepParent", str(tmp_path / "OpenBot.app"), str(archive)],
        check=True,
    )
    dest = tmp_path / "out"
    dest.mkdir()
    extract_archive(archive, dest)
    payload = find_payload(dest, platform_name="darwin")
    marker = payload / "Contents" / "Frameworks" / "marker.txt"
    assert marker.is_symlink()
    assert marker.read_text(encoding="utf-8") == "ok"
