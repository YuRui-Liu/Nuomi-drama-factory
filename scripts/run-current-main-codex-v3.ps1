[CmdletBinding()]
param(
    [string]$CodexCommand = "codex",
    [switch]$Interactive
)

$ErrorActionPreference = "Stop"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$global:OutputEncoding = $utf8NoBom

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryDirectory = Split-Path -Parent $scriptDirectory
$promptPath = Join-Path $repositoryDirectory "docs\plans\2026-08-30-current-main-cli-executor-prompt.md"

if (-not (Test-Path -LiteralPath $promptPath -PathType Leaf)) {
    throw "Codex executor prompt not found: $promptPath"
}

$codexExecutable = Get-Command $CodexCommand -ErrorAction SilentlyContinue
if ($null -eq $codexExecutable) {
    throw "Codex CLI was not found. Install it with 'npm install -g @openai/codex@latest', or pass -CodexCommand with the full executable path."
}

$prompt = [System.IO.File]::ReadAllText($promptPath, $utf8NoBom)
Set-Location -LiteralPath $repositoryDirectory

$cliVersion = (& $codexExecutable.Source --version 2>&1 | Out-String).Trim()
Write-Host "Repository: $repositoryDirectory"
Write-Host "Codex CLI:  $($codexExecutable.Source)"
Write-Host "CLI version:$cliVersion"
Write-Host "Prompt:     $promptPath"

if ($Interactive) {
    & $codexExecutable.Source $prompt
    exit $LASTEXITCODE
}

$execHelp = (& $codexExecutable.Source exec --help 2>&1 | Out-String)
$execArguments = [System.Collections.Generic.List[string]]::new()
$execArguments.Add("exec")

if ($execHelp -match "--full-auto") {
    Write-Host "Write mode: --full-auto (CLI compatibility mode)"
    $execArguments.Add("--full-auto")
}
elseif ($execHelp -match "--sandbox") {
    Write-Host "Write mode: --sandbox workspace-write"
    $execArguments.Add("--sandbox")
    $execArguments.Add("workspace-write")
}
else {
    throw "This Codex CLI exposes neither --full-auto nor --sandbox for 'codex exec'. Upgrade @openai/codex or use -Interactive."
}

$runLogDirectory = Join-Path $repositoryDirectory ".codex-cli-runs"
New-Item -ItemType Directory -Path $runLogDirectory -Force | Out-Null
$runStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$jsonLogPath = Join-Path $runLogDirectory "current-main-$runStamp.jsonl"
$lastMessagePath = Join-Path $runLogDirectory "current-main-$runStamp-final.md"

if ($execHelp -match "--json") {
    $execArguments.Add("--json")
}
if ($execHelp -match "--output-last-message") {
    $execArguments.Add("--output-last-message")
    $execArguments.Add($lastMessagePath)
}
$execArguments.Add("-")

Write-Host "JSON log:   $jsonLogPath"
Write-Host "Final note: $lastMessagePath"

$prompt | & $codexExecutable.Source @execArguments | Tee-Object -FilePath $jsonLogPath
exit $LASTEXITCODE

