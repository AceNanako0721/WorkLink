param([string]$Config, [string]$PluginPath)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
if (-not $Config) { $Config = Join-Path (Split-Path $taskRoot -Parent) 'WorkLink-private/config.json' }
$taskConfigPath = (Resolve-Path -LiteralPath $Config).Path
$taskPrivateRoot = Split-Path $taskConfigPath -Parent
python -X utf8 -c "import pathlib,sys; root=pathlib.Path(sys.argv[1]).resolve(); config=pathlib.Path(sys.argv[2]).resolve(); agent=(config.parent/'omp-agent').resolve(); sys.exit(1 if any(p==root or root in p.parents for p in (config,agent)) else 0)" $taskRoot $taskConfigPath
if ($LASTEXITCODE -ne 0) { throw 'Use a configuration and OMP profile outside the checkout.' }
if (-not $PluginPath) { $PluginPath = Join-Path $taskPrivateRoot 'upstream/oh-my-pi-telegram' }
if (-not (Get-Command omp -ErrorAction SilentlyContinue)) { throw 'OMP is not installed on this host. Install or select the intended OMP host first.' }
$taskSettings = Get-Content -LiteralPath $taskConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$taskAgentDir = Join-Path $taskPrivateRoot 'omp-agent'
if (-not (Test-Path -LiteralPath (Join-Path $taskAgentDir 'telegram.json'))) { throw 'Prepare the dedicated external OMP profile first.' }
python -X utf8 (Join-Path $PSScriptRoot 'patch-omp-plugin.py') $PluginPath
if ($LASTEXITCODE -ne 0) { throw 'Pinned plugin verification failed.' }
$taskPreviousBase = $env:WORKLINK_TELEGRAM_API_BASE
$taskPreviousAgent = $env:PI_CODING_AGENT_DIR
$taskPreviousFamily = $env:PI_TELEGRAM_NETWORK_FAMILY
try {
    $env:WORKLINK_TELEGRAM_API_BASE = 'http://127.0.0.1:' + $taskSettings.service.port
    $env:PI_CODING_AGENT_DIR = $taskAgentDir
    $env:PI_TELEGRAM_NETWORK_FAMILY = 'ipv4'
    Write-Host 'OMP will use the local WorkLink endpoint and the dedicated external profile.'
    Write-Host 'Use /telegram-connect in OMP after its model credentials and plugin are ready.'
    & omp
    if ($LASTEXITCODE -ne 0) { throw 'OMP stopped with an error.' }
} finally {
    $env:WORKLINK_TELEGRAM_API_BASE = $taskPreviousBase
    $env:PI_CODING_AGENT_DIR = $taskPreviousAgent
    $env:PI_TELEGRAM_NETWORK_FAMILY = $taskPreviousFamily
}
