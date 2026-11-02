$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$TonyPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $TonyPython)) { throw 'Create .venv and install requirements-runtime-win.lock first.' }
& $TonyPython -m app.manage serve
