param(
    [switch]$EnableAutostart
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$BuildDir = Join-Path $RepoRoot "build"
$DistAppDir = Join-Path $RepoRoot "dist\DailyOverlay"
$IconPath = Join-Path $BuildDir "daily_overlay.ico"

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

Write-Host "1. Generating application icon ($IconPath)..."
$IconScript = @"
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap

app = QGuiApplication([])
icon = QIcon()
for size in (16, 32, 48, 64, 128, 256):
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    pad = max(1, size // 10)
    p.setBrush(QColor("#6FB1D6"))
    p.drawEllipse(pad, pad, size - 2 * pad, size - 2 * pad)
    p.setBrush(QColor("#E08E67"))
    inner = int(size * 0.36)
    p.drawEllipse(int(size * 0.34), int(size * 0.28), inner, inner)
    p.end()
    icon.addPixmap(pix)

pix_out = icon.pixmap(256, 256)
ok = pix_out.save(r"$IconPath", "ICO")
assert ok, "Failed to write ICO"
print("ICON_OK")
"@
$IconScript | uv run --no-sync python -
if ($LASTEXITCODE -ne 0) {
    throw "Failed to generate application icon."
}

Write-Host "2. Building windowless DailyOverlay.exe with PyInstaller (--onedir --windowed)..."
uv run --no-sync --with pyinstaller pyinstaller `
    --onedir `
    --windowed `
    --noconfirm `
    --clean `
    --name DailyOverlay `
    --icon $IconPath `
    --paths src `
    --collect-submodules websockets `
    src\daily_overlay\main.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

$VenvImageFormats = Join-Path $RepoRoot ".venv\Lib\site-packages\PySide6\plugins\imageformats"
$DistImageFormats = Join-Path $DistAppDir "_internal\PySide6\plugins\imageformats"
$DistQWebp = Join-Path $DistImageFormats "qwebp.dll"

if (-not (Test-Path $DistQWebp)) {
    Write-Host "3. Copying PySide6 imageformats plugins (including qwebp.dll)..."
    if (-not (Test-Path $VenvImageFormats)) {
        throw "Could not find PySide6 imageformats folder at $VenvImageFormats"
    }
    New-Item -ItemType Directory -Force -Path $DistImageFormats | Out-Null
    Copy-Item -Path (Join-Path $VenvImageFormats "*") -Destination $DistImageFormats -Force
}

if (-not (Test-Path $DistQWebp)) {
    throw "qwebp.dll is still missing at $DistQWebp"
}
Write-Host "   Verified WebP plugin: $DistQWebp"

Write-Host "4. Populating runtime data and extension next to DailyOverlay.exe..."
$DistDataDir = Join-Path $DistAppDir "data"
$DistAssetsDir = Join-Path $DistDataDir "assets"
$DistExtDir = Join-Path $DistAppDir "browser_extension"

New-Item -ItemType Directory -Force -Path $DistAssetsDir | Out-Null
New-Item -ItemType Directory -Force -Path $DistExtDir | Out-Null

$SrcCatalog = Join-Path $RepoRoot "data\assets\catalog.json"
if (Test-Path $SrcCatalog) {
    Copy-Item -Path $SrcCatalog -Destination (Join-Path $DistAssetsDir "catalog.json") -Force
}

$WebpFiles = Get-ChildItem -Path (Join-Path $RepoRoot "data\assets") -Filter "*.webp" -ErrorAction SilentlyContinue
if ($WebpFiles) {
    foreach ($f in $WebpFiles) {
        Copy-Item -Path $f.FullName -Destination (Join-Path $DistAssetsDir $f.Name) -Force
    }
    Write-Host "   Copied $($WebpFiles.Count) cached WebP assets into dist/DailyOverlay/data/assets/"
}

Copy-Item -Path (Join-Path $RepoRoot "browser_extension\*") -Destination $DistExtDir -Recurse -Force

$SrcProfiles = Join-Path $RepoRoot "data\app_profiles.json"
if (Test-Path $SrcProfiles) {
    Copy-Item -Path $SrcProfiles -Destination (Join-Path $DistDataDir "app_profiles.json") -Force
}

$SrcEnv = Join-Path $RepoRoot ".env"
if (Test-Path $SrcEnv) {
    Copy-Item -Path $SrcEnv -Destination (Join-Path $DistAppDir ".env") -Force
    Write-Host "   Copied local .env into dist/DailyOverlay/.env (gitignored & local-only)"
} else {
    Write-Warning "No .env found in repo root. Copy .env.example to dist\DailyOverlay\.env and set GOOGLE_AI_API_KEY."
}

$DistExe = Join-Path $DistAppDir "DailyOverlay.exe"
if (-not (Test-Path $DistExe)) {
    throw "DailyOverlay.exe not found at $DistExe"
}

Write-Host "5. Running --self-check from TEMP directory..."
$DistLog = Join-Path $DistDataDir "daily_overlay.log"
if (Test-Path $DistLog) {
    Remove-Item $DistLog -Force
}

Push-Location $env:TEMP
try {
    $Proc = Start-Process -FilePath $DistExe -ArgumentList "--self-check" -Wait -PassThru -NoNewWindow
    if ($Proc.ExitCode -ne 0) {
        throw "DailyOverlay.exe --self-check exited with code $($Proc.ExitCode)"
    }
} finally {
    Pop-Location
}

if (-not (Test-Path $DistLog)) {
    throw "Expected log file not found at $DistLog"
}
$LogContent = Get-Content -Path $DistLog -Raw
if ($LogContent -notmatch "SELF_CHECK_OK") {
    throw "SELF_CHECK_OK not found in $DistLog`n$LogContent"
}
Write-Host "   Verified SELF_CHECK_OK in $DistLog"

if ($EnableAutostart) {
    Write-Host "6. Registering DailyOverlay in Windows Startup (HKCU Run)..."
    $RegPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    if (-not (Test-Path $RegPath)) {
        New-Item -Path $RegPath -Force | Out-Null
    }
    Set-ItemProperty -Path $RegPath -Name "DailyOverlay" -Value "`"$DistExe`""
    Write-Host "   Startup entry set -> `"$DistExe`""
}

Write-Host ""
Write-Host "BUILD_APP_OK: $DistExe"
