# 为 pyarrow 在 Windows 上补齐 IANA 时区数据库
# pyarrow 默认去 %USERPROFILE%\Downloads\tzdata 找 tzdata 和 windowsZones.xml
# 用法（以 PowerShell 执行）：
#   powershell -ExecutionPolicy Bypass -File scripts\install_tzdata.ps1

$ErrorActionPreference = "Stop"

$TzDir = Join-Path $env:USERPROFILE "Downloads\tzdata"
New-Item -ItemType Directory -Force -Path $TzDir | Out-Null

Write-Host "Target dir: $TzDir"

# 1. IANA tzdata
$TzArchive = Join-Path $TzDir "tzdata.tar.gz"
Write-Host "Downloading tzdata-latest.tar.gz ..."
Invoke-WebRequest -Uri "https://data.iana.org/time-zones/tzdata-latest.tar.gz" -OutFile $TzArchive -UseBasicParsing
tar -xzf $TzArchive -C $TzDir
Remove-Item $TzArchive -Force

# 2. CLDR windowsZones.xml（Windows 时区名 → IANA 映射）
Write-Host "Downloading windowsZones.xml ..."
Invoke-WebRequest `
    -Uri "https://raw.githubusercontent.com/unicode-org/cldr/master/common/supplemental/windowsZones.xml" `
    -OutFile (Join-Path $TzDir "windowsZones.xml") `
    -UseBasicParsing

Write-Host ""
Write-Host "Done. Files in $TzDir :"
Get-ChildItem $TzDir | Select-Object Name, Length | Format-Table

Write-Host ""
Write-Host "Restart the MCBG server (Ctrl+C then 'python run.py') for it to take effect."
