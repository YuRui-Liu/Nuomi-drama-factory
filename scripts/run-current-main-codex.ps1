[CmdletBinding()]
param(
    [string]$CodexCommand = "codex",
    [switch]$Interactive
)

$ErrorActionPreference = "Stop"

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryDirectory = Split-Path -Parent $scriptDirectory
$promptPath = Join-Path $repositoryDirectory "docs\plans\2026-08-30-current-main-cli-executor-prompt.md"

if (-not (Test-Path -LiteralPath $promptPath -PathType Leaf)) {
    throw "Codex executor prompt not found: $promptPath"
}

$codexExecutable = Get-Command $CodexCommand -ErrorAction SilentlyContinue
if ($null -eq $codexExecutable) {
    throw "Codex CLI was not found. Install it with 'npm install -g @openai/codex', or pass -CodexCommand with the full executable path."
}

$prompt = Get-Content -LiteralPath $promptPath -Raw
Set-Location -LiteralPath $repositoryDirectory

Write-Host "Repository: $repositoryDirectory"
Write-Host "Codex CLI:  $($codexExecutable.Source)"
Write-Host "Prompt:     $promptPath"

if ($Interactive) {
    & $codexExecutable.Source --cd $repositoryDirectory $prompt
    exit $LASTEXITCODE
}

$runLogDirectory = Join-Path $repositoryDirectory ".codex-cli-runs"
New-Item -ItemType Directory -Path $runLogDirectory -Force | Out-Null
$runStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$jsonLogPath = Join-Path $runLogDirectory "current-main-$runStamp.jsonl"
$lastMessagePath = Join-Path $runLogDirectory "current-main-$runStamp-final.md"

Write-Host "JSON log:   $jsonLogPath"
Write-Host "Final note: $lastMessagePath"

$prompt | & $codexExecutable.Source exec `
    --cd $repositoryDirectory `
    --sandbox workspace-write `
    --ask-for-approval on-failure `
    --json `
    --output-last-message $lastMessagePath `
    - | Tee-Object -FilePath $jsonLogPath

exit $LASTEXITCODE

