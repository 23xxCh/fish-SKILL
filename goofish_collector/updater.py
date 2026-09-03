from __future__ import annotations

import hashlib
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen


LATEST_RELEASE_URL = "https://api.github.com/repos/23xxCh/fish-SKILL/releases/latest"
PACKAGE_PREFIX = "XianyuLinkCollector-v"
PACKAGE_SUFFIX = "-windows.zip"
MAX_RELEASE_BYTES = 2 * 1024 * 1024 * 1024
_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    release_url: str
    notes: str
    asset_name: str
    asset_url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class PreparedUpdate:
    info: UpdateInfo
    package_dir: Path


def _version_key(version: str) -> tuple[int, int, int]:
    match = _VERSION_PATTERN.fullmatch(version.strip())
    if not match:
        raise ValueError(f"无法识别发布版本：{version!r}")
    return tuple(int(value) for value in match.groups())


def _fetch_latest_release() -> dict:
    request = Request(
        LATEST_RELEASE_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "GoofishLinkCollector-Updater",
        },
    )
    with urlopen(request, timeout=15) as response:  # nosec B310 - fixed GitHub API URL
        import json

        return json.loads(response.read().decode("utf-8"))


def _download(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "GoofishLinkCollector-Updater"})
    received = 0
    with urlopen(request, timeout=60) as response, destination.open("wb") as output:  # nosec B310
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_RELEASE_BYTES:
            raise ValueError("更新包超过允许的大小")
        while chunk := response.read(1024 * 1024):
            received += len(chunk)
            if received > MAX_RELEASE_BYTES:
                raise ValueError("更新包超过允许的大小")
            output.write(chunk)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class UpdateService:
    """Checks only official GitHub releases and stages a verified update locally."""

    def __init__(
        self,
        current_version: str,
        *,
        release_fetcher: Callable[[], dict] = _fetch_latest_release,
        downloader: Callable[[str, Path], None] = _download,
    ) -> None:
        self.current_version = current_version
        self._release_fetcher = release_fetcher
        self._downloader = downloader

    def check_for_update(self) -> UpdateInfo | None:
        release = self._release_fetcher()
        if release.get("draft") or release.get("prerelease"):
            raise ValueError("最新发布不是稳定正式版")
        version = release.get("tag_name", "")
        if _version_key(version) <= _version_key(self.current_version):
            return None
        normalized_version = version.removeprefix("v")
        expected_name = f"{PACKAGE_PREFIX}{normalized_version}{PACKAGE_SUFFIX}"
        asset = next(
            (candidate for candidate in release.get("assets", []) if candidate.get("name") == expected_name),
            None,
        )
        if not asset:
            raise ValueError(f"发布中没有匹配的 Windows 更新包：{expected_name}")
        digest = asset.get("digest", "")
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            raise ValueError("发布包缺少可验证的 SHA-256 摘要")
        asset_url = asset.get("browser_download_url", "")
        parsed_url = urlparse(asset_url)
        if parsed_url.scheme != "https" or not parsed_url.hostname or not parsed_url.hostname.endswith("github.com"):
            raise ValueError("更新包不是 GitHub HTTPS 下载地址")
        size = asset.get("size", 0)
        if not isinstance(size, int) or not 0 < size <= MAX_RELEASE_BYTES:
            raise ValueError("发布包大小无效")
        return UpdateInfo(
            version=normalized_version,
            release_url=release.get("html_url", ""),
            notes=release.get("body", "").strip(),
            asset_name=expected_name,
            asset_url=asset_url,
            sha256=digest.partition(":")[2].lower(),
            size=size,
        )

    def prepare_update(self, update: UpdateInfo | None, update_root: Path) -> PreparedUpdate:
        if update is None:
            raise ValueError("没有可安装的更新")
        update_root.mkdir(parents=True, exist_ok=True)
        staging_root = Path(tempfile.mkdtemp(prefix=f"v{update.version}-", dir=update_root))
        archive_path = staging_root / update.asset_name
        try:
            self._downloader(update.asset_url, archive_path)
            actual_digest = _file_sha256(archive_path)
            if actual_digest.lower() != update.sha256:
                raise ValueError("更新包 SHA-256 校验失败，已取消安装")
            extract_root = staging_root / "extracted"
            self._safe_extract(archive_path, extract_root)
            package_dir = extract_root / "XianyuLinkCollector"
            executable = package_dir / "XianyuLinkCollector.exe"
            if not executable.is_file():
                raise ValueError("更新包目录结构不正确，已取消安装")
            return PreparedUpdate(info=update, package_dir=package_dir)
        except Exception:
            shutil.rmtree(staging_root, ignore_errors=True)
            raise

    @staticmethod
    def _safe_extract(archive_path: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        root = destination.resolve()
        with zipfile.ZipFile(archive_path) as archive:
            for item in archive.infolist():
                name = item.filename.replace("\\", "/")
                relative = PurePosixPath(name)
                if relative.is_absolute() or ".." in relative.parts or not name:
                    raise ValueError("更新包包含不安全的文件路径")
                if stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError("更新包不能包含链接文件")
                target = (destination / Path(*relative.parts)).resolve()
                if not target.is_relative_to(root):
                    raise ValueError("更新包包含不安全的文件路径")
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)

    @staticmethod
    def build_install_script(
        *,
        parent_pid: int,
        package_dir: Path,
        install_dir: Path,
        executable_name: str,
    ) -> str:
        def literal(value: Path | str) -> str:
            return str(value).replace("'", "''")

        return f"""$ErrorActionPreference = 'Stop'
$parentPid = {parent_pid}
$package = '{literal(package_dir)}'
$install = '{literal(install_dir)}'
$executable = '{literal(executable_name)}'
while (Get-Process -Id $parentPid -ErrorAction SilentlyContinue) {{ Start-Sleep -Milliseconds 250 }}
$backup = "$install.backup-$(Get-Date -Format 'yyyyMMddHHmmss')"
if (Test-Path -LiteralPath $install) {{ Move-Item -LiteralPath $install -Destination $backup }}
try {{
    Copy-Item -LiteralPath $package -Destination (Split-Path -Parent $install) -Recurse -Force
    $newExecutable = Join-Path $install $executable
    if (-not (Test-Path -LiteralPath $newExecutable)) {{ throw '更新后的程序文件不完整。' }}
    Start-Process -FilePath $newExecutable
}}
catch {{
    if (Test-Path -LiteralPath $install) {{ Remove-Item -LiteralPath $install -Recurse -Force }}
    if (Test-Path -LiteralPath $backup) {{ Move-Item -LiteralPath $backup -Destination $install }}
    throw
}}
"""

    @classmethod
    def launch_installer(
        cls,
        prepared: PreparedUpdate,
        *,
        install_dir: Path,
        parent_pid: int,
        launcher: Callable[..., object] = subprocess.Popen,
    ) -> Path:
        script_path = prepared.package_dir.parent.parent / "apply-update.ps1"
        script_path.write_text(
            cls.build_install_script(
                parent_pid=parent_pid,
                package_dir=prepared.package_dir,
                install_dir=install_dir,
                executable_name="XianyuLinkCollector.exe",
            ),
            encoding="utf-8",
        )
        launcher(["powershell.exe", "-NoProfile", "-File", str(script_path)])
        return script_path
