$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$python = if ($env:PYTHON) {
    $env:PYTHON
} elseif (Test-Path -LiteralPath (Join-Path $root ".venv-manager\Scripts\python.exe")) {
    Join-Path $root ".venv-manager\Scripts\python.exe"
} else {
    (Get-Command python -ErrorAction Stop).Source
}
& $python (Join-Path $root "tools\bazaar_spine_manager_ui.py")
