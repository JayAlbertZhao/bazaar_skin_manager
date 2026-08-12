param(
    [string]$BepInExDir = "D:\SteamLibrary\steamapps\common\The Bazaar\BepInEx",
    [string]$UnityEditorDir = "D:\Program Files\Unity 2022.3.57f1c2\Editor",
    [string]$Configuration = "Release",
    [string]$Version = "1.4.10"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$msbuild = "C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe"
$project = Join-Path $projectRoot "src\BazaarSkinManager.Runtime\BazaarSkinManager.Runtime.csproj"
$dist = Join-Path $projectRoot "dist\runtime"

if (-not (Test-Path -LiteralPath $msbuild)) {
    throw "MSBuild not found: $msbuild"
}
if (-not (Test-Path -LiteralPath (Join-Path $BepInExDir "core\BepInEx.dll"))) {
    throw "BepInEx reference not found: $BepInExDir"
}
if (-not (Test-Path -LiteralPath (Join-Path $UnityEditorDir "Data\Managed\UnityEngine\UnityEngine.CoreModule.dll"))) {
    throw "Unity reference assemblies not found: $UnityEditorDir"
}

& $msbuild $project `
    /t:Rebuild `
    /p:Configuration=$Configuration `
    /p:BepInExDir=$BepInExDir `
    /p:UnityEditorDir=$UnityEditorDir `
    /nologo `
    /verbosity:minimal

if ($LASTEXITCODE -ne 0) {
    throw "MSBuild failed with exit code $LASTEXITCODE"
}

New-Item -ItemType Directory -Force -Path $dist | Out-Null
$builtDll = Join-Path $projectRoot "src\BazaarSkinManager.Runtime\bin\$Configuration\BazaarSkinManager.Runtime.dll"
Copy-Item -LiteralPath $builtDll -Destination (Join-Path $dist "BazaarSkinManager.Runtime.dll") -Force
$runtimeDll = Join-Path $dist "BazaarSkinManager.Runtime.dll"
$metadata = [ordered]@{
    schema_version = 1
    component = "runtime-adapter"
    version = $Version
    executable = "BazaarSkinManager.Runtime.dll"
    sha256 = (Get-FileHash -LiteralPath $runtimeDll -Algorithm SHA256).Hash.ToLowerInvariant()
    bytes = (Get-Item -LiteralPath $runtimeDll).Length
}
$metadata | ConvertTo-Json -Depth 3 |
    Set-Content -LiteralPath (Join-Path $dist "runtime-build.json") -Encoding utf8
Write-Host "Built: $(Join-Path $dist 'BazaarSkinManager.Runtime.dll')"
