# Pack the Chrome extension into a distributable zip.
# Usage (from anywhere):
#   powershell -ExecutionPolicy Bypass -File scripts\pack_extension.ps1

$ErrorActionPreference = "Stop"

$scriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$extDir      = Join-Path $projectRoot "extension"
$manifestPath = Join-Path $extDir "manifest.json"

if (-not (Test-Path $manifestPath)) {
    Write-Error "manifest not found: $manifestPath"
    exit 1
}

# Parse version via regex to avoid PS 5.1 ConvertFrom-Json BOM issues.
$raw = Get-Content $manifestPath -Raw -Encoding UTF8
$m = [regex]::Match($raw, '"version"\s*:\s*"([^"]+)"')
if (-not $m.Success) {
    Write-Error "cannot parse version from $manifestPath"
    exit 1
}
$version = $m.Groups[1].Value

$outName = "mcbg-extension-v$version.zip"
$outPath = Join-Path $projectRoot $outName

if (Test-Path $outPath) {
    Remove-Item $outPath -Force
}

# Expand files explicitly to avoid Compress-Archive wildcard quirks on PS 5.1.
$items = Get-ChildItem -Path $extDir -Force | ForEach-Object { $_.FullName }
if (-not $items) {
    Write-Error "no files under $extDir"
    exit 1
}
Compress-Archive -Path $items -DestinationPath $outPath -CompressionLevel Optimal

$sizeKB = [math]::Round((Get-Item $outPath).Length / 1KB, 1)

Write-Host ""
Write-Host "[OK] packed: $outPath ($sizeKB KB)" -ForegroundColor Green
Write-Host "Share the zip. Recipient unzips then loads the 'extension' folder via chrome://extensions/"
