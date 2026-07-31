param(
    [string]$Version = "0.9.4"
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

& $python -c "import archspec, PIL, PyInstaller, tkinterdnd2, UnityPy"
if ($LASTEXITCODE -ne 0) {
    throw "Manager build dependencies are incomplete. Install manager\requirements-build.txt into .venv-manager."
}

New-Item -ItemType Directory -Force -Path $output, $work, $spec | Out-Null

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
    --add-binary "$fmodDll;fmod_toolkit\libfmod\Windows\x64" `
    --add-data "$root\manager\hero-catalog.json;manager" `
    --add-data "$root\manager\adapters\mak-default.json;manager\adapters" `
    --add-data "$runtime;dist\runtime" `
    --add-data "$runtimeMetadata;dist\runtime" `
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

$metadata = [ordered]@{
    schema_version = 1
    version = $Version
    executable = "TheBazaarModManager.exe"
    sha256 = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    bytes = (Get-Item -LiteralPath $exe).Length
}
$metadata | ConvertTo-Json -Depth 3 |
    Set-Content -LiteralPath (Join-Path $output "manager-build.json") -Encoding utf8

Write-Host "Built manager: $exe"
