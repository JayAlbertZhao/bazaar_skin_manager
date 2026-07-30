param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [string]$CertificatePath,
    [Parameter(Mandatory = $true)]
    [string]$CertificatePassword,
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
$target = [IO.Path]::GetFullPath($Path)
$certificate = [IO.Path]::GetFullPath($CertificatePath)
foreach ($required in @($target, $certificate)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Signing input not found: $required"
    }
}
$kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
$signTool = Get-ChildItem -LiteralPath $kits -Filter signtool.exe -File -Recurse |
    Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
    Sort-Object FullName -Descending |
    Select-Object -First 1
if (-not $signTool) {
    throw "signtool.exe was not found in the Windows 10 SDK."
}

& $signTool.FullName sign `
    /fd SHA256 `
    /f $certificate `
    /p $CertificatePassword `
    /tr $TimestampUrl `
    /td SHA256 `
    $target
if ($LASTEXITCODE -ne 0) {
    throw "signtool failed with exit code $LASTEXITCODE"
}
& $signTool.FullName verify /pa /v $target
if ($LASTEXITCODE -ne 0) {
    throw "Signature verification failed with exit code $LASTEXITCODE"
}

