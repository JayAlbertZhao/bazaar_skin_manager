$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$builds = Get-ChildItem -LiteralPath (Join-Path $root "dist") -Directory -Filter "asset-generator*" -ErrorAction SilentlyContinue |
    ForEach-Object {
        $metadataPath = Join-Path $_.FullName "asset-generator-build.json"
        if (Test-Path -LiteralPath $metadataPath) {
            $metadata = Get-Content -Raw -LiteralPath $metadataPath | ConvertFrom-Json
            $executable = Join-Path $_.FullName $metadata.executable
            if (Test-Path -LiteralPath $executable) {
                [pscustomobject]@{
                    Version = [version]$metadata.version
                    Executable = $executable
                }
            }
        }
    } |
    Sort-Object Version -Descending
$built = $builds | Select-Object -First 1
if ($null -ne $built) {
    Start-Process -FilePath $built.Executable -WorkingDirectory $root
    exit 0
}
$python = Join-Path $root ".venv-manager\Scripts\pythonw.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Asset generator environment is missing: $python"
}
Start-Process -FilePath $python -ArgumentList (Join-Path $root "tools\asset_generator_ui.py") -WorkingDirectory $root
