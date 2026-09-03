import hashlib
import shutil
import zipfile
from pathlib import Path

import pytest

from goofish_collector.updater import UpdateService


def _release_payload(*, version: str, digest: str, size: int = 1) -> dict:
    return {
        "tag_name": f"v{version}",
        "html_url": f"https://github.com/23xxCh/fish-SKILL/releases/tag/v{version}",
        "draft": False,
        "prerelease": False,
        "body": "修复内容",
        "assets": [
            {
                "name": f"XianyuLinkCollector-v{version}-windows.zip",
                "browser_download_url": "https://github.com/23xxCh/fish-SKILL/releases/download/v0.7.2/package.zip",
                "digest": f"sha256:{digest}",
                "size": size,
            }
        ],
    }


def test_check_selects_only_a_newer_release_with_a_matching_sha256() -> None:
    digest = "a" * 64
    service = UpdateService("0.7.1", release_fetcher=lambda: _release_payload(version="0.7.2", digest=digest))

    update = service.check_for_update()

    assert update is not None
    assert update.version == "0.7.2"
    assert update.sha256 == digest
    assert update.asset_name == "XianyuLinkCollector-v0.7.2-windows.zip"


def test_check_ignores_current_or_older_release() -> None:
    service = UpdateService(
        "0.7.2",
        release_fetcher=lambda: _release_payload(version="0.7.2", digest="a" * 64),
    )

    assert service.check_for_update() is None


def test_check_rejects_a_release_missing_the_verified_package_digest() -> None:
    payload = _release_payload(version="0.7.2", digest="a" * 64)
    del payload["assets"][0]["digest"]
    service = UpdateService("0.7.1", release_fetcher=lambda: payload)

    with pytest.raises(ValueError, match="SHA-256"):
        service.check_for_update()


def test_prepare_download_verifies_digest_and_extracts_only_the_packaged_app(tmp_path: Path) -> None:
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("XianyuLinkCollector/XianyuLinkCollector.exe", b"executable")
        bundle.writestr("XianyuLinkCollector/_internal/app.dll", b"library")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    service = UpdateService(
        "0.7.1",
        release_fetcher=lambda: _release_payload(
            version="0.7.2", digest=digest, size=archive.stat().st_size
        ),
        downloader=lambda _url, destination: shutil.copyfile(archive, destination),
    )

    prepared = service.prepare_update(service.check_for_update(), tmp_path / "updates")

    assert prepared.package_dir.name == "XianyuLinkCollector"
    assert (prepared.package_dir / "XianyuLinkCollector.exe").read_bytes() == b"executable"


def test_prepare_download_rejects_zip_paths_outside_the_update_staging_directory(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../outside.txt", b"unsafe")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    service = UpdateService(
        "0.7.1",
        release_fetcher=lambda: _release_payload(
            version="0.7.2", digest=digest, size=archive.stat().st_size
        ),
        downloader=lambda _url, destination: shutil.copyfile(archive, destination),
    )

    with pytest.raises(ValueError, match="不安全"):
        service.prepare_update(service.check_for_update(), tmp_path / "updates")


def test_install_helper_keeps_a_timestamped_backup_and_restarts_the_new_executable(tmp_path: Path) -> None:
    script = UpdateService.build_install_script(
        parent_pid=123,
        package_dir=tmp_path / "staged" / "XianyuLinkCollector",
        install_dir=tmp_path / "installed" / "XianyuLinkCollector",
        executable_name="XianyuLinkCollector.exe",
    )

    assert "backup-" in script
    assert "Copy-Item -LiteralPath" in script
    assert "Start-Process -FilePath" in script
    assert "Remove-Item -LiteralPath" in script
