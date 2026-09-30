param([int]$Port = 8765, [string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($PythonPath) {
    $python = Get-Command $PythonPath -ErrorAction Stop
} else {
    $python = Get-Command py -ErrorAction SilentlyContinue
    if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
}
if (-not $python) {
    Write-Host 'Python 3.10 or later is required: https://www.python.org/downloads/'
    exit 1
}
$versionArgs = @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)')
if ($python.Name -eq 'py.exe') { $versionArgs = @('-3') + $versionArgs }
& $python.Source @versionArgs
if ($LASTEXITCODE -ne 0) { Write-Host 'Python 3.10 or later is required.'; exit 1 }
$runArgs = @((Join-Path $PSScriptRoot 'app.py'), '--port', "$Port", '--open')
if ($python.Name -eq 'py.exe') { $runArgs = @('-3') + $runArgs }
& $python.Source @runArgs
exit $LASTEXITCODE
