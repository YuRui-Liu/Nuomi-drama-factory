[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 90,
    [switch]$SkipLiveProbe
)

$ErrorActionPreference = "Stop"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$global:OutputEncoding = $utf8NoBom

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryDirectory = Split-Path -Parent $scriptDirectory
$reportDirectory = Join-Path $repositoryDirectory ".codex-cli-runs"
New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null

$runStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$reportPath = Join-Path $reportDirectory "runtime-doctor-$runStamp.json"
$probePromptPath = Join-Path $reportDirectory "runtime-doctor-$runStamp-prompt.txt"

function Find-CodexLauncher {
    $whereResults = @(& where.exe codex 2>$null)
    $cmdCandidate = $whereResults | Where-Object { $_ -match "\.cmd$" } | Select-Object -First 1
    if ($cmdCandidate) {
        return [ordered]@{
            path = $cmdCandidate
            kind = "cmd"
            command = "call `"$cmdCandidate`""
            candidates = @($whereResults)
        }
    }

    $command = Get-Command codex -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        return $null
    }

    if ($command.Source -match "\.ps1$") {
        return [ordered]@{
            path = $command.Source
            kind = "powershell"
            command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$($command.Source)`""
            candidates = @($whereResults)
        }
    }

    return [ordered]@{
        path = $command.Source
        kind = "native"
        command = "`"$($command.Source)`""
        candidates = @($whereResults)
    }
}

function Invoke-ProbeCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Launcher,
        [Parameter(Mandatory = $true)][string]$Arguments,
        [Parameter(Mandatory = $true)][string]$Name,
        [int]$Timeout = 30,
        [string]$InputPath
    )

    $stdoutPath = Join-Path $reportDirectory "runtime-doctor-$runStamp-$Name.stdout.txt"
    $stderrPath = Join-Path $reportDirectory "runtime-doctor-$runStamp-$Name.stderr.txt"
    $inputRedirect = if ($InputPath) { " < `"$InputPath`"" } else { "" }
    $commandLine = "chcp 65001 >nul & $Launcher $Arguments$inputRedirect"

    $startedAt = Get-Date
    $process = Start-Process `
        -FilePath $env:ComSpec `
        -ArgumentList @("/d", "/s", "/c", $commandLine) `
        -WorkingDirectory $repositoryDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru

    $timedOut = -not $process.WaitForExit($Timeout * 1000)
    if ($timedOut) {
        try { $process.Kill() } catch { }
        $process.WaitForExit()
    }

    $stdout = if (Test-Path -LiteralPath $stdoutPath) {
        [System.IO.File]::ReadAllText($stdoutPath, $utf8NoBom)
    } else { "" }
    $stderr = if (Test-Path -LiteralPath $stderrPath) {
        [System.IO.File]::ReadAllText($stderrPath, $utf8NoBom)
    } else { "" }

    return [ordered]@{
        name = $Name
        arguments = $Arguments
        exitCode = if ($timedOut) { $null } else { $process.ExitCode }
        timedOut = $timedOut
        durationMs = [int]((Get-Date) - $startedAt).TotalMilliseconds
        stdout = $stdout
        stderr = $stderr
        stdoutPath = $stdoutPath
        stderrPath = $stderrPath
    }
}

function Add-Issue {
    param(
        [System.Collections.Generic.List[object]]$Issues,
        [string]$Code,
        [string]$Severity,
        [string]$Message,
        [string]$Recommendation
    )
    $Issues.Add([ordered]@{
        code = $Code
        severity = $Severity
        message = $Message
        recommendation = $Recommendation
    })
}

$issues = [System.Collections.Generic.List[object]]::new()
$launcher = Find-CodexLauncher

$report = [ordered]@{
    schemaVersion = 1
    generatedAt = (Get-Date).ToString("o")
    repository = $repositoryDirectory
    host = [ordered]@{
        os = [System.Environment]::OSVersion.VersionString
        powershell = $PSVersionTable.PSVersion.ToString()
        processArchitecture = $env:PROCESSOR_ARCHITECTURE
    }
    launcher = $launcher
    probes = [ordered]@{}
    issues = $issues
    healthy = $false
}

if ($null -eq $launcher) {
    Add-Issue $issues "CLI_NOT_FOUND" "fatal" "Codex CLI was not found on PATH." "Run: npm install -g @openai/codex@latest"
    $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    Write-Host "[FATAL] Codex CLI not found."
    Write-Host "Report: $reportPath"
    exit 10
}

Write-Host "Nuomi Codex Runtime Doctor"
Write-Host "Repository: $repositoryDirectory"
Write-Host "Launcher:   $($launcher.path)"

$versionProbe = Invoke-ProbeCommand -Launcher $launcher.command -Arguments "--version" -Name "version" -Timeout 20
$helpProbe = Invoke-ProbeCommand -Launcher $launcher.command -Arguments "exec --help" -Name "exec-help" -Timeout 20
$loginProbe = Invoke-ProbeCommand -Launcher $launcher.command -Arguments "login status" -Name "login-status" -Timeout 30
$report.probes.version = $versionProbe
$report.probes.execHelp = $helpProbe
$report.probes.login = [ordered]@{
    exitCode = $loginProbe.exitCode
    timedOut = $loginProbe.timedOut
    durationMs = $loginProbe.durationMs
    statusDetected = ($loginProbe.stdout + $loginProbe.stderr) -match "logged|login|authenticated|ChatGPT|API"
    stdoutPath = $loginProbe.stdoutPath
    stderrPath = $loginProbe.stderrPath
}

if ($versionProbe.timedOut -or $versionProbe.exitCode -ne 0) {
    Add-Issue $issues "CLI_START_FAILED" "fatal" "Codex CLI could not return its version." "Reinstall or upgrade: npm install -g @openai/codex@latest"
}

$execHelp = $helpProbe.stdout + $helpProbe.stderr
$capabilities = [ordered]@{
    fullAuto = $execHelp -match "--full-auto"
    sandbox = $execHelp -match "--sandbox"
    json = $execHelp -match "--json"
    outputLastMessage = $execHelp -match "--output-last-message"
    skipGitRepoCheck = $execHelp -match "--skip-git-repo-check"
}
$report.capabilities = $capabilities

if (-not $capabilities.fullAuto -and -not $capabilities.sandbox) {
    Add-Issue $issues "NO_WRITE_MODE" "fatal" "The installed CLI exposes no recognized writable automation mode." "Upgrade: npm install -g @openai/codex@latest"
}

if ($loginProbe.timedOut -or $loginProbe.exitCode -ne 0) {
    Add-Issue $issues "AUTH_UNHEALTHY" "fatal" "Codex login status failed or timed out." "Run: codex login"
}

if (-not $SkipLiveProbe -and $issues.Count -eq 0) {
    [System.IO.File]::WriteAllText($probePromptPath, "回复且仅回复：RUNTIME_OK_中文", $utf8NoBom)
    $probeArguments = [System.Collections.Generic.List[string]]::new()
    $probeArguments.Add("exec")
    if ($capabilities.sandbox) {
        $probeArguments.Add("--sandbox")
        $probeArguments.Add("read-only")
    }
    if ($capabilities.json) {
        $probeArguments.Add("--json")
    }
    $probeArguments.Add("-")

    $liveProbe = Invoke-ProbeCommand `
        -Launcher $launcher.command `
        -Arguments ($probeArguments -join " ") `
        -Name "live" `
        -Timeout $TimeoutSeconds `
        -InputPath $probePromptPath
    $report.probes.live = $liveProbe

    $combined = $liveProbe.stdout + "`n" + $liveProbe.stderr
    if ($combined -match "missing field [`']?base_instructions") {
        Add-Issue $issues "MODEL_CACHE_SCHEMA_MISMATCH" "fatal" "The local models cache uses an incompatible schema (base_instructions missing)." "Upgrade Codex CLI, close Codex Desktop, back up %USERPROFILE%\.codex\models_cache.json, then retry."
    }
    if ($combined -match "timeout waiting for child process to exit") {
        Add-Issue $issues "MODEL_REFRESH_TIMEOUT" "fatal" "The model catalog refresh child process timed out." "Upgrade Codex CLI and avoid running a mismatched Desktop/CLI pair against the same model cache."
    }
    if ($liveProbe.timedOut) {
        Add-Issue $issues "LIVE_PROBE_TIMEOUT" "fatal" "The read-only Codex turn did not finish within $TimeoutSeconds seconds." "Inspect the live stderr report and repair model cache/auth before running implementation."
    }
    if (-not $liveProbe.timedOut -and $liveProbe.exitCode -ne 0) {
        Add-Issue $issues "LIVE_PROBE_FAILED" "fatal" "The read-only Codex turn exited with code $($liveProbe.exitCode)." "Inspect the live stdout/stderr paths in this report."
    }
    if ($combined -notmatch "RUNTIME_OK_中文") {
        if ($combined -match "RUNTIME_OK" -or $combined -match "\?\?\?\?") {
            Add-Issue $issues "UTF8_TRANSPORT_BROKEN" "fatal" "The runtime did not preserve the Chinese probe text." "Use the V3 launcher, chcp 65001, and UTF-8 without BOM for stdin."
        }
        elseif (-not $liveProbe.timedOut -and $liveProbe.exitCode -eq 0) {
            Add-Issue $issues "LIVE_PROBE_NO_RESULT" "fatal" "The turn exited without the expected runtime marker." "Inspect JSONL output for interruption or model selection failures."
        }
    }
}

$report.healthy = $issues.Count -eq 0
$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding UTF8

Write-Host ""
if ($report.healthy) {
    Write-Host "[PASS] Codex runtime is healthy, authenticated, UTF-8 safe, and completed a live read-only turn." -ForegroundColor Green
    Write-Host "Next: scripts\run-current-main-codex-v3.cmd"
    $exitCode = 0
}
else {
    Write-Host "[FAIL] Codex runtime is not ready." -ForegroundColor Red
    foreach ($issue in $issues) {
        Write-Host "[$($issue.severity.ToUpper())] $($issue.code): $($issue.message)"
        Write-Host "  Fix: $($issue.recommendation)"
    }
    $exitCode = 1
}

Write-Host "Report: $reportPath"
exit $exitCode

