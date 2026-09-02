param(
    [switch]$SkipTests,
    [string]$DistPath = "dist"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $projectRoot
try {
    if (-not $SkipTests) {
        python -m pytest -q
        if ($LASTEXITCODE -ne 0) {
            throw "Tests failed; build stopped."
        }
    }

    python -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('packaging.requirements') else 1)"
    if ($LASTEXITCODE -ne 0) {
        python -m pip install --force-reinstall "packaging==26.0"
        if ($LASTEXITCODE -ne 0) {
            throw "Python packaging dependency repair failed."
        }
    }

    python -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('PyInstaller') else 1)"
    if ($LASTEXITCODE -ne 0) {
        python -m pip install "PyInstaller>=6.15,<7"
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller installation failed."
        }
    }

    $resolvedDistPath = Join-Path $projectRoot $DistPath
    python -m PyInstaller --noconfirm --clean --distpath $resolvedDistPath XianyuLinkCollector.spec
    if ($LASTEXITCODE -ne 0) {
        throw "Desktop application build failed."
    }

    $executable = Join-Path $resolvedDistPath "XianyuLinkCollector\XianyuLinkCollector.exe"
    if (-not (Test-Path -LiteralPath $executable)) {
        throw "Build completed without executable: $executable"
    }
    Copy-Item -LiteralPath (Join-Path $projectRoot "README.md") -Destination (Join-Path (Split-Path -Parent $executable) "README.md") -Force
    Write-Host "Build completed: $executable"
}
finally {
    Pop-Location
}
