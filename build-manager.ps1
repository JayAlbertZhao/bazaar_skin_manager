param(
    [string]$Version = "1.4.13"
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$bundledPython = Join-Path $root ".venv-manager\Scripts\python.exe"
$python = if ($env:PYTHON) {
    if (Test-Path -LiteralPath $env:PYTHON) {
        $env:PYTHON
    } else {
        (Get-Command $env:PYTHON -ErrorAction Stop).Source
    }
} elseif (Test-Path -LiteralPath $bundledPython) {
    $bundledPython
} else {
    (Get-Command python -ErrorAction Stop).Source
}
$entry = Join-Path $root "tools\bazaar_skin_manager_ui.py"
$output = Join-Path $root "dist\manager"
$work = Join-Path $root ".codex-work\pyinstaller-manager"
$spec = Join-Path $root ".codex-work\pyinstaller-spec"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Manager build environment is missing: $python"
}
$sitePackages = & $python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
if ($LASTEXITCODE -ne 0 -or -not $sitePackages) {
    throw "Could not locate the manager build environment's site-packages directory."
}
$fmodDll = Join-Path $sitePackages.Trim() "fmod_toolkit\libfmod\Windows\x64\fmod.dll"
if (-not (Test-Path -LiteralPath $fmodDll)) {
    throw "FMOD runtime required by UnityPy is missing: $fmodDll"
}

$distRuntime = Join-Path $root "dist\runtime\BazaarSkinManager.Runtime.dll"
$distRuntimeMetadata = Join-Path $root "dist\runtime\runtime-build.json"
$embeddedRuntime = Join-Path $root "manager\runtime\BazaarSkinManager.Runtime.dll"
$runtime = if (
    (Test-Path -LiteralPath $distRuntime) -and
    (Test-Path -LiteralPath $distRuntimeMetadata)
) {
    $distRuntime
} else {
    $embeddedRuntime
}
if (-not (Test-Path -LiteralPath $runtime)) {
    throw "Release runtime is missing: manager\runtime\BazaarSkinManager.Runtime.dll"
}
$runtimeMetadata = Join-Path (Split-Path -Parent $runtime) "runtime-build.json"
if (-not (Test-Path -LiteralPath $runtimeMetadata)) {
    throw "Release runtime metadata is missing: $runtimeMetadata"
}
$runtimeRelease = Get-Content -LiteralPath $runtimeMetadata -Raw |
    ConvertFrom-Json
$runtimeHash = (Get-FileHash -LiteralPath $runtime -Algorithm SHA256).Hash.ToLowerInvariant()
if ($runtimeRelease.sha256 -ne $runtimeHash) {
    throw "Release runtime metadata SHA-256 does not match: $runtime"
}
Write-Host "Bundling runtime adapter $($runtimeRelease.version): $runtime"

$bepInExDirectory = Join-Path $root "third_party\BepInEx"
$bepInExArchive = Join-Path $bepInExDirectory "BepInEx_win_x64_5.4.23.5.zip"
$bepInExLicense = Join-Path $bepInExDirectory "LICENSE.txt"
$bepInExNotice = Join-Path $bepInExDirectory "NOTICE.md"
$bepInExExpectedHash = "82f9878551030f54657792c0740d9d51a09500eeae1fba21106b0c441e6732c4"
foreach ($required in @($bepInExArchive, $bepInExLicense, $bepInExNotice)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "BepInEx bootstrap release input is missing: $required"
    }
}
$bepInExHash = (Get-FileHash -LiteralPath $bepInExArchive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($bepInExHash -ne $bepInExExpectedHash) {
    throw "BepInEx bootstrap SHA-256 does not match: $bepInExArchive"
}
Write-Host "Bundling official BepInEx 5.4.23.5 bootstrap: $bepInExArchive"

& $python -c "import archspec, PIL, PyInstaller, tkinterdnd2, UnityPy"
if ($LASTEXITCODE -ne 0) {
    throw "Manager build dependencies are incomplete. Install manager\requirements-build.txt into .venv-manager."
}

New-Item -ItemType Directory -Force -Path $output, $work, $spec | Out-Null
$managerAssets = Join-Path $root "manager\assets"
$managerAssetArguments = @()
if (Test-Path -LiteralPath $managerAssets -PathType Container) {
    $managerAssetArguments = @("--add-data", "$managerAssets;manager\assets")
} else {
    Write-Warning "Game-derived manager previews are absent; building without optional preview assets."
}

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "TheBazaarModManager" `
    --distpath $output `
    --workpath $work `
    --specpath $spec `
    --collect-all tkinterdnd2 `
    --collect-all UnityPy `
    --collect-all archspec `
    --hidden-import unity_bundle_texture_patch `
    --hidden-import spine_manager_core `
    --add-binary "$fmodDll;fmod_toolkit\libfmod\Windows\x64" `
    --add-data "$root\manager\hero-catalog.json;manager" `
    --add-data "$root\manager\audio-route-catalog.json;manager" `
    --add-data "$root\manager\adapters;manager\adapters" `
    $managerAssetArguments `
    --add-data "$runtime;dist\runtime" `
    --add-data "$runtimeMetadata;dist\runtime" `
    --add-data "$bepInExArchive;third_party\BepInEx" `
    --add-data "$bepInExLicense;third_party\BepInEx" `
    --add-data "$bepInExNotice;third_party\BepInEx" `
    --add-data "$root\tools\unity_bundle_texture_patch.py;tools" `
    $entry

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$exe = Join-Path $output "TheBazaarModManager.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "Manager executable was not produced: $exe"
}
$selfTest = Start-Process `
    -FilePath $exe `
    -ArgumentList "--self-test-release-runtime" `
    -Wait `
    -PassThru `
    -WindowStyle Hidden
if ($selfTest.ExitCode -ne 0) {
    throw "Frozen release runtime self-test failed with exit code $($selfTest.ExitCode)"
}
$bepInExSelfTest = Start-Process `
    -FilePath $exe `
    -ArgumentList "--self-test-bepinex-bootstrap" `
    -Wait `
    -PassThru `
    -WindowStyle Hidden
if ($bepInExSelfTest.ExitCode -ne 0) {
    throw "Frozen BepInEx bootstrap self-test failed with exit code $($bepInExSelfTest.ExitCode)"
}
$previousLocalAppData = $env:LOCALAPPDATA
try {
    $env:LOCALAPPDATA = Join-Path $work "self-test-localappdata"
    $uiSelfTest = Start-Process `
        -FilePath $exe `
        -ArgumentList "--self-test-v12-ui" `
        -Wait `
        -PassThru `
        -WindowStyle Hidden
    if ($uiSelfTest.ExitCode -ne 0) {
        throw "Frozen integrated UI self-test failed with exit code $($uiSelfTest.ExitCode)"
    }
} finally {
    $env:LOCALAPPDATA = $previousLocalAppData
}

$adapterCapabilities = @(
    Get-ChildItem -LiteralPath (Join-Path $root "manager\adapters") -Filter "*.json" |
        Sort-Object Name |
        ForEach-Object {
            $adapterPath = $_.FullName
            $adapter = Get-Content -LiteralPath $adapterPath -Raw -Encoding utf8 |
                ConvertFrom-Json
            [ordered]@{
                id = [string]$adapter.id
                adapter_version = [int]$adapter.adapter_version
                hero = [string]$adapter.target.hero
                skin = [string]$adapter.target.skin
                sha256 = (Get-FileHash -LiteralPath $adapterPath -Algorithm SHA256).Hash.ToLowerInvariant()
                authoring_recipe_id = if ($adapter.authoring_recipe) {
                    [string]$adapter.authoring_recipe.id
                } else {
                    $null
                }
                authoring_recipe_version = if ($adapter.authoring_recipe) {
                    [int]$adapter.authoring_recipe.version
                } else {
                    $null
                }
            }
        }
)
$metadata = [ordered]@{
    schema_version = 1
    version = $Version
    executable = "TheBazaarModManager.exe"
    sha256 = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    bytes = (Get-Item -LiteralPath $exe).Length
    adapters = $adapterCapabilities
}
$metadata | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath (Join-Path $output "manager-build.json") -Encoding utf8

Write-Host "Built manager: $exe"
