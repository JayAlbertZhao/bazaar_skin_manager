param(
    [string]$Version = "1.5.1",
    [string]$OutputDirectory = ""
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
$entry = Join-Path $root "tools\bazaar_spine_manager_ui.py"
$output = if ($OutputDirectory) {
    [IO.Path]::GetFullPath($OutputDirectory)
} else {
    Join-Path $root "dist\spine-manager"
}
$work = Join-Path $root ".codex-work\pyinstaller-spine-manager"
$spec = Join-Path $root ".codex-work\pyinstaller-spec"
$runtimeDirectory = if (
    (Test-Path -LiteralPath (Join-Path $root "dist\runtime\BazaarSkinManager.Runtime.dll")) -and
    (Test-Path -LiteralPath (Join-Path $root "dist\runtime\runtime-build.json"))
) {
    Join-Path $root "dist\runtime"
} else {
    Join-Path $root "manager\runtime"
}
$converterVersion = "v3.8"
$converterSha256 = "b2ca82e46f1f4ca463abf0ccfab32e3c01eb0dd89fc7289b6478f728ca8ed68a"
$converterDirectory = Join-Path $root ".codex-work\spine-converter\$converterVersion"
$converterExe = Join-Path $converterDirectory "SpineSkeletonDataConverter.exe"
$converterUrl = "https://github.com/wang606/SpineSkeletonDataConverter/releases/download/$converterVersion/SpineSkeletonDataConverter.exe"
$converterLicense = Join-Path $root "third_party\SpineSkeletonDataConverter-LICENSE.txt"

New-Item -ItemType Directory -Force -Path $converterDirectory | Out-Null
if (-not (Test-Path -LiteralPath $converterExe) -or
    (Get-FileHash -Algorithm SHA256 -LiteralPath $converterExe).Hash.ToLowerInvariant() -ne $converterSha256) {
    $download = "$converterExe.download"
    Remove-Item -Force -ErrorAction SilentlyContinue $download
    Invoke-WebRequest -UseBasicParsing -Uri $converterUrl -OutFile $download
    $downloadHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $download).Hash.ToLowerInvariant()
    if ($downloadHash -ne $converterSha256) {
        Remove-Item -Force -ErrorAction SilentlyContinue $download
        throw "Spine converter hash mismatch: $downloadHash"
    }
    Move-Item -Force $download $converterExe
}
if (-not (Test-Path -LiteralPath $converterLicense)) {
    throw "Spine converter license notice is missing: $converterLicense"
}

$sitePackages = & $python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
$fmodDll = Join-Path $sitePackages.Trim() "fmod_toolkit\libfmod\Windows\x64\fmod.dll"
if (-not (Test-Path -LiteralPath $fmodDll)) {
    throw "FMOD runtime required by UnityPy is missing: $fmodDll"
}

New-Item -ItemType Directory -Force -Path $output, $work, $spec | Out-Null
$arguments = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "TheBazaarSpineManager",
    "--distpath", $output,
    "--workpath", $work,
    "--specpath", $spec,
    "--collect-all", "UnityPy",
    "--collect-all", "archspec",
    "--hidden-import", "spine_manager_core",
    "--hidden-import", "spine_static_preview",
    "--hidden-import", "adapter_registry",
    "--hidden-import", "bazaar_skin_manager",
    "--add-binary", "$fmodDll;fmod_toolkit\libfmod\Windows\x64",
    "--add-binary", "$converterExe;spine-converter",
    "--add-data", "$converterLicense;spine-converter",
    "--add-data", "$root\manager\hero-catalog.json;manager",
    "--add-data", "$root\manager\adapters;manager\adapters",
    "--add-data", "$root\manager\spine-preview;manager\spine-preview",
    "--add-data", "$runtimeDirectory;dist\runtime",
    $entry
)
& $python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Spine Manager PyInstaller build failed with exit code $LASTEXITCODE"
}

$exe = Join-Path $output "TheBazaarSpineManager.exe"
$smoke = Start-Process -FilePath $exe -ArgumentList "--self-test" -Wait -PassThru -WindowStyle Hidden
if ($smoke.ExitCode -ne 0) {
    throw "Frozen Spine Manager self-test failed with exit code $($smoke.ExitCode)"
}

$metadata = [ordered]@{
    version = $Version
    file = (Split-Path -Leaf $exe)
    bytes = (Get-Item -LiteralPath $exe).Length
    sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $exe).Hash.ToLowerInvariant()
}
$metadata | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $output "spine-manager-build.json")
Write-Host "Built $exe"
