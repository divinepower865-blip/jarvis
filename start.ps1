$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$jarvisPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $jarvisPython)) {
    throw 'Install the environment using README.md first.'
}
& $jarvisPython -m jarvis serve
