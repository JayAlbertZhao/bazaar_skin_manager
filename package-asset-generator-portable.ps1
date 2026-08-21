param(
    [string]$Version = "1.5.2"
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$dist = Join-Path $root "dist"
$generator = Join-Path $dist "asset-generator\TheBazaarAssetGenerator.exe"
$generatorMetadata = Join-Path $dist "asset-generator\asset-generator-build.json"
$quickStart = Join-Path $root "docs\asset-generator-quick-start.txt"
$staging = Join-Path $dist "asset-generator-portable-staging"
$archive = Join-Path $dist "TheBazaarAssetGenerator-Portable-$Version.zip"

$resolvedStaging = [IO.Path]::GetFullPath($staging)
if (-not $resolvedStaging.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe staging path: $resolvedStaging"
}

foreach ($required in @($generator, $generatorMetadata, $quickStart)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required portable-package input is missing: $required"
    }
}

if (Test-Path -LiteralPath $staging) {
    Remove-Item -LiteralPath $staging -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $staging | Out-Null

Copy-Item -LiteralPath $generator -Destination $staging
Copy-Item -LiteralPath $generatorMetadata -Destination $staging
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
    product = "The Bazaar Asset Generator Portable"
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
Write-Host "Packaged portable asset generator: $archive"
Write-Host "SHA256: $hash"
