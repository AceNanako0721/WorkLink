param([Parameter(Mandatory=$true)][ValidatePattern('^\d+\.\d+\.\d+(-[a-z0-9.-]+)?$')][string]$Version)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Invoke-Checked([string]$Tool, [string[]]$Arguments) {
    & $Tool @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Tool failed; release stopped." }
}
if ((git branch --show-current) -ne 'main') { throw 'Release from main.' }
if (git status --porcelain) { throw 'Commit or preserve changes before releasing.' }
Invoke-Checked 'gh' @('auth','status')
Invoke-Checked 'git' @('fetch','origin','main','--tags')
git show-ref --verify --quiet "refs/tags/v$Version"
if ($LASTEXITCODE -eq 0) { throw 'Version tag already exists.' }
Invoke-Checked 'git' @('merge-base','--is-ancestor','origin/main','HEAD')
Invoke-Checked 'python' @('scripts/check-public.py','--history')
Invoke-Checked 'python' @('-m','unittest','discover','-s','tests','-v')
if ((Get-Content VERSION -Raw).Trim() -ne $Version) {
    [System.IO.File]::WriteAllText((Join-Path (Get-Location) 'VERSION'), "$Version`n", [System.Text.UTF8Encoding]::new($false))
    Invoke-Checked 'git' @('add','--','VERSION')
    Invoke-Checked 'git' @('commit','-m',"Release v$Version")
}
Invoke-Checked 'python' @('scripts/check-public.py','--history')
Invoke-Checked 'git' @('tag',"v$Version")
Invoke-Checked 'git' @('push','--atomic','origin','main',"refs/tags/v$Version")
Write-Host 'Version pushed. GitHub Actions will check, package and publish the Release.'
