param(
    [string]$Version = "1.3.0"
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$script = Join-Path $root "installer\TheBazaarModManager.iss"
$output = Join-Path $root "dist\installer"
$candidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$compiler = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $compiler) {
    throw "Inno Setup 6 was not found. Install JRSoftware.InnoSetup, then rerun."
}
if (-not (Test-Path -LiteralPath (Join-Path $root "dist\manager\TheBazaarModManager.exe"))) {
    & (Join-Path $root "build-manager.ps1") -Version $Version
}
if (-not (Test-Path -LiteralPath (Join-Path $root "dist\asset-generator\TheBazaarAssetGenerator.exe"))) {
    & (Join-Path $root "build-asset-generator.ps1") -Version $Version
}
if (-not (Test-Path -LiteralPath (Join-Path $root "dist\spine-manager\TheBazaarSpineManager.exe"))) {
    & (Join-Path $root "build-spine-manager.ps1") -Version $Version
}

New-Item -ItemType Directory -Force -Path $output | Out-Null
& $compiler "/DMyAppVersion=$Version" "/DSourceRoot=$root" "/O$output" $script
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE"
}

$installer = Join-Path $output "TheBazaarModManager-Setup-$Version.exe"
if (-not (Test-Path -LiteralPath $installer)) {
    throw "Installer was not produced: $installer"
}
$hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  $([IO.Path]::GetFileName($installer))" |
    Set-Content -LiteralPath "$installer.sha256" -Encoding ascii
Write-Host "Built installer: $installer"
Write-Host "SHA256: $hash"
