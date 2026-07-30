param()

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$venvPython = Join-Path $root ".venv-manager\Scripts\pythonw.exe"
$basePython = "pythonw.exe"
$entry = Join-Path $root "tools\bazaar_skin_manager_ui.py"

if (Test-Path -LiteralPath $venvPython) {
    Start-Process -FilePath $venvPython -ArgumentList @($entry) -WorkingDirectory $root
    exit 0
}

Start-Process -FilePath $basePython -ArgumentList @($entry) -WorkingDirectory $root
