param(
    [string]$Version = "1.3.2"
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$dist = Join-Path $root "dist"
$manager = Join-Path $dist "manager\TheBazaarModManager.exe"
$managerMetadata = Join-Path $dist "manager\manager-build.json"
$quickStart = Join-Path $root "docs\portable-quick-start.txt"
$staging = Join-Path $dist "manager-portable-staging"
$archive = Join-Path $dist "TheBazaarModManager-Portable-$Version.zip"

$resolvedStaging = [IO.Path]::GetFullPath($staging)
if (-not $resolvedStaging.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe staging path: $resolvedStaging"
}

foreach ($required in @($manager, $managerMetadata, $quickStart)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required portable-package input is missing: $required"
    }
}

if (Test-Path -LiteralPath $staging) {
    Remove-Item -LiteralPath $staging -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $staging | Out-Null

Copy-Item -LiteralPath $manager -Destination $staging
Copy-Item -LiteralPath $managerMetadata -Destination $staging
Copy-Item -LiteralPath $quickStart -Destination (Join-Path $staging "README.txt")

$files = Get-ChildItem -LiteralPath $staging -File -Recurse |
    Sort-Object FullName |
    ForEach-Object {
        $relative = [IO.Path]::GetFullPath($_.FullName).Substring($staging.Length)
        $relative = $relative.TrimStart([char[]]@('\', '/')).Replace("\", "/")
        [ordered]@{
            path = $relative
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            bytes = $_.Length
        }
    }
$manifest = [ordered]@{
    schema_version = 1
    product = "The Bazaar Skin Manager Portable"
    version = $Version
    files = $files
}
$manifest | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath (Join-Path $staging "portable-manifest.json") -Encoding utf8

if (Test-Path -LiteralPath $archive) {
    Remove-Item -LiteralPath $archive -Force
}
Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $archive

$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  $([IO.Path]::GetFileName($archive))" |
    Set-Content -LiteralPath "$archive.sha256" -Encoding ascii
Write-Host "Packaged portable manager: $archive"
Write-Host "SHA256: $hash"
