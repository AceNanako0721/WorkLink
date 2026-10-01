param([string]$Config)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
if (-not $Config) { $Config = Join-Path (Split-Path $taskRoot -Parent) 'WorkLink-private/config.json' }
$taskPreviousPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $taskRoot 'src'
    python -u -X utf8 -m worklink --config $Config
    if ($LASTEXITCODE -ne 0) { throw 'WorkLink stopped with an error.' }
} finally {
    $env:PYTHONPATH = $taskPreviousPath
}
