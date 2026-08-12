param(
    [string]$Version = "1.4.10",
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
$entry = Join-Path $root "tools\asset_generator_ui.py"
$output = if ($OutputDirectory) {
    [IO.Path]::GetFullPath($OutputDirectory)
} else {
    Join-Path $root "dist\asset-generator"
}
$work = Join-Path $root ".codex-work\pyinstaller-asset-generator"
$spec = Join-Path $root ".codex-work\pyinstaller-spec"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Asset generator build environment is missing: $python"
}
$sitePackages = & $python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
$fmodDll = Join-Path $sitePackages.Trim() "fmod_toolkit\libfmod\Windows\x64\fmod.dll"
if (-not (Test-Path -LiteralPath $fmodDll)) {
    throw "FMOD runtime required by UnityPy is missing: $fmodDll"
}

$runtime = Join-Path $root "dist\runtime\BazaarSkinManager.Runtime.dll"
$runtimeMetadata = Join-Path $root "dist\runtime\runtime-build.json"
if (-not (Test-Path -LiteralPath $runtime)) {
    $runtime = Join-Path $root "manager\runtime\BazaarSkinManager.Runtime.dll"
    $runtimeMetadata = Join-Path $root "manager\runtime\runtime-build.json"
}
if (-not (Test-Path -LiteralPath $runtime) -or -not (Test-Path -LiteralPath $runtimeMetadata)) {
    throw "A verified Skin Manager runtime is required for deployment."
}

New-Item -ItemType Directory -Force -Path $output, $work, $spec | Out-Null
$pyInstallerArguments = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "TheBazaarAssetGenerator",
    "--distpath", $output,
    "--workpath", $work,
    "--specpath", $spec,
    "--collect-all", "tkinterdnd2",
    "--collect-all", "UnityPy",
    "--collect-all", "archspec",
    "--hidden-import", "unity_bundle_texture_patch",
    "--add-binary", "$fmodDll;fmod_toolkit\libfmod\Windows\x64",
    "--add-data", "$root\manager\hero-catalog.json;manager",
    "--add-data", "$root\manager\audio-route-catalog.json;manager",
    "--add-data", "$root\manager\adapters;manager\adapters",
    "--add-data", "$runtime;dist\runtime",
    "--add-data", "$runtimeMetadata;dist\runtime",
    "--add-data", "$root\tools\unity_bundle_texture_patch.py;tools"
)
$badgeAssets = Join-Path $root "manager\assets"
if (Test-Path -LiteralPath $badgeAssets -PathType Container) {
    # Locally prepared badge templates may be bundled in private/netdisk
    # builds. Public source and GitHub releases intentionally omit game art.
    $pyInstallerArguments += @("--add-data", "$badgeAssets;manager\assets")
}
$pyInstallerArguments += $entry
& $python @pyInstallerArguments
if ($LASTEXITCODE -ne 0) {
    throw "Asset generator PyInstaller build failed with exit code $LASTEXITCODE"
}

$exe = Join-Path $output "TheBazaarAssetGenerator.exe"
$smoke = Start-Process -FilePath $exe -ArgumentList "--self-test" -Wait -PassThru -WindowStyle Hidden
if ($smoke.ExitCode -ne 0) {
    throw "Frozen asset generator self-test failed with exit code $($smoke.ExitCode)"
}
$metadata = [ordered]@{
    schema_version = 1
    version = $Version
    executable = "TheBazaarAssetGenerator.exe"
    sha256 = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    bytes = (Get-Item -LiteralPath $exe).Length
}
$metadata | ConvertTo-Json -Depth 3 |
    Set-Content -LiteralPath (Join-Path $output "asset-generator-build.json") -Encoding utf8
$resolvedWork = [IO.Path]::GetFullPath($work)
$resolvedSpec = [IO.Path]::GetFullPath((Join-Path $spec "TheBazaarAssetGenerator.spec"))
$safeRoot = $root.TrimEnd('\') + '\'
if ($resolvedWork.StartsWith($safeRoot, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $resolvedWork)) {
    Remove-Item -LiteralPath $resolvedWork -Recurse -Force
}
if ($resolvedSpec.StartsWith($safeRoot, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $resolvedSpec)) {
    Remove-Item -LiteralPath $resolvedSpec -Force
}
$resolvedSpecDirectory = [IO.Path]::GetFullPath($spec)
if (
    $resolvedSpecDirectory.StartsWith($safeRoot, [StringComparison]::OrdinalIgnoreCase) -and
    (Test-Path -LiteralPath $resolvedSpecDirectory) -and
    -not (Get-ChildItem -LiteralPath $resolvedSpecDirectory -Force)
) {
    Remove-Item -LiteralPath $resolvedSpecDirectory -Force
}
Write-Host "Built asset generator: $exe"
