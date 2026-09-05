[CmdletBinding()]
param(
    [int]$ProbeTimeoutSeconds = 120,
    [switch]$SkipProbe
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $scriptDir
$runDir = Join-Path $repo '.codex-cli-runs'
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$reportPath = Join-Path $runDir "runtime-doctor-v2-$stamp.json"
$lastMessagePath = Join-Path $runDir "runtime-doctor-v2-$stamp-last.md"

function Find-Codex {
    $paths = @()
    try {
        $paths += Get-Command codex -All -ErrorAction Stop |
            ForEach-Object Source |
            Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    } catch {}
    try {
        $paths += & where.exe codex 2>$null |
            Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    } catch {}

    @($paths | Select-Object -Unique) |
        Sort-Object @{Expression={
            switch ([IO.Path]::GetExtension($_).ToLowerInvariant()) {
                '.cmd' { 0 }; '.exe' { 1 }; '.ps1' { 2 }; default { 3 }
            }
        }}, @{Expression={$_}} |
        Select-Object -First 1
}

function Invoke-Codex {
    param(
        [string]$Launcher,
        [string[]]$CliArgs,
        [int]$TimeoutSeconds
    )

    $payload = [pscustomobject]@{
        Launcher = $Launcher
        CliArgs = @($CliArgs)
        WorkingDirectory = $repo
    }
    $job = Start-Job -ScriptBlock {
        param($Payload)
        $jobUtf8 = New-Object System.Text.UTF8Encoding($false)
        [Console]::InputEncoding = $jobUtf8
        [Console]::OutputEncoding = $jobUtf8
        $OutputEncoding = $jobUtf8
        $env:PYTHONUTF8 = '1'
        $env:PYTHONIOENCODING = 'utf-8'
        Set-Location -LiteralPath ([string]$Payload.WorkingDirectory)

        $lines = @()
        try {
            $launcherPath = [string]$Payload.Launcher
            $nativeArgs = @($Payload.CliArgs | ForEach-Object { [string]$_ })
            $lines = @(& $launcherPath @nativeArgs 2>&1 | ForEach-Object { [string]$_ })
            $code = $LASTEXITCODE
            if ($null -eq $code) { $code = 0 }
            [pscustomobject]@{
                ExitCode = [int]$code
                Output = $lines -join [Environment]::NewLine
                ErrorText = ''
            }
        } catch {
            [pscustomobject]@{
                ExitCode = -1
                Output = $lines -join [Environment]::NewLine
                ErrorText = $_.Exception.Message
            }
        }
    } -ArgumentList (,$payload)

    try {
        if (-not (Wait-Job -Job $job -Timeout $TimeoutSeconds)) {
            Stop-Job -Job $job -ErrorAction SilentlyContinue
            return [pscustomobject]@{ ExitCode=-2; Output=''; ErrorText=''; TimedOut=$true }
        }
        $received = Receive-Job -Job $job
        if ($null -eq $received) {
            return [pscustomobject]@{ ExitCode=-1; Output=''; ErrorText='Child returned no result'; TimedOut=$false }
        }
        [pscustomobject]@{
            ExitCode = [int]$received.ExitCode
            Output = [string]$received.Output
            ErrorText = [string]$received.ErrorText
            TimedOut = $false
        }
    } finally {
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    }
}

function Issue([string]$Severity, [string]$Code, [string]$Message, [string]$Fix='') {
    [pscustomobject]@{ severity=$Severity; code=$Code; message=$Message; fix=$Fix }
}

function Add-KnownIssues([string]$Text, [System.Collections.ArrayList]$IssueList) {
    if ($Text -match 'missing field\s+[`''\"]?base_instructions') {
        [void]$IssueList.Add((Issue fatal MODEL_CACHE_SCHEMA_MISMATCH 'The CLI cannot read the current models cache schema.' 'Close Codex processes, upgrade @openai/codex, rename models_cache.json to a timestamped .bak, then retry.'))
    }
    if ($Text -match 'timeout waiting for child process to exit') {
        [void]$IssueList.Add((Issue fatal MODEL_REFRESH_TIMEOUT 'The model refresh child process timed out.' 'Close other Codex processes, then retry after upgrading the CLI.'))
    }
    if ($Text -match 'not logged in|authentication required|unauthorized|\b401\b') {
        [void]$IssueList.Add((Issue fatal AUTH_REQUIRED 'The live probe reports an authentication error.' 'Run codex login, then retry.'))
    }
}

$issues = New-Object System.Collections.ArrayList
$launcher = Find-Codex

Write-Host 'Nuomi Codex Runtime Doctor v2'
Write-Host "Repository: $repo"
Write-Host "Launcher:   $launcher"

if (-not $launcher) {
    [void]$issues.Add((Issue fatal CLI_NOT_FOUND 'Codex was not found on PATH.' 'Install: npm install -g @openai/codex@latest'))
    $report = [ordered]@{ schema_version=2; ready=$false; repository=$repo; launcher=$null; issues=@($issues) }
    [IO.File]::WriteAllText($reportPath, ($report | ConvertTo-Json -Depth 8), $utf8)
    Write-Host '[FAIL] Codex CLI not found.' -ForegroundColor Red
    Write-Host "Report: $reportPath"
    exit 1
}

$version = Invoke-Codex $launcher @('--version') 20
$help = Invoke-Codex $launcher @('exec','--help') 30
$login = Invoke-Codex $launcher @('login','status') 30
$preflightText = @($version.Output,$version.ErrorText,$help.Output,$help.ErrorText,$login.Output,$login.ErrorText) -join "`n"
Add-KnownIssues $preflightText $issues

if ($version.TimedOut) {
    [void]$issues.Add((Issue fatal VERSION_TIMEOUT 'codex --version timed out.' 'Inspect the report before reinstalling.'))
} elseif ($version.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($version.Output)) {
    [void]$issues.Add((Issue fatal VERSION_FAILED 'codex --version returned no usable result.' 'Inspect version.output and version.error_text in the report.'))
}
if ($help.TimedOut -or $help.ExitCode -ne 0) {
    [void]$issues.Add((Issue fatal EXEC_HELP_FAILED 'codex exec --help failed.' 'Inspect exec_help in the report.'))
}
if ($login.TimedOut -or $login.ExitCode -ne 0) {
    [void]$issues.Add((Issue warning LOGIN_STATUS_UNAVAILABLE 'login status is unavailable; this alone does not mean authentication is broken.' 'The live exec probe is the source of truth.'))
}

$helpText = [string]$help.Output
$caps = [ordered]@{
    sandbox = $helpText -match '(?m)^\s*(?:-\w,\s*)?--sandbox\b'
    json = $helpText -match '(?m)^\s*(?:-\w,\s*)?--json\b'
    output_last_message = $helpText -match '(?m)^\s*(?:-\w,\s*)?--output-last-message\b'
    skip_git_repo_check = $helpText -match '(?m)^\s*(?:-\w,\s*)?--skip-git-repo-check\b'
}

$probe = [ordered]@{ skipped=[bool]$SkipProbe; args=@(); exit_code=$null; timed_out=$false; marker_found=$false; output=''; error_text=''; last_message_path=$lastMessagePath }
if (-not $SkipProbe -and $help.ExitCode -eq 0) {
    $probeArgs = New-Object System.Collections.Generic.List[string]
    $probeArgs.Add('exec')
    if ($caps.sandbox) { $probeArgs.Add('--sandbox'); $probeArgs.Add('read-only') }
    if ($caps.json) { $probeArgs.Add('--json') }
    if ($caps.output_last_message) { $probeArgs.Add('--output-last-message'); $probeArgs.Add($lastMessagePath) }
    if ($caps.skip_git_repo_check) { $probeArgs.Add('--skip-git-repo-check') }
    $probeArgs.Add('Reply with exactly: RUNTIME_OK_中文')

    $probe.args = @($probeArgs)
    Write-Host "Live probe: timeout ${ProbeTimeoutSeconds}s"
    $probeResult = Invoke-Codex $launcher @($probeArgs) $ProbeTimeoutSeconds
    $probe.exit_code = $probeResult.ExitCode
    $probe.timed_out = $probeResult.TimedOut
    $probe.output = $probeResult.Output
    $probe.error_text = $probeResult.ErrorText
    $lastMessage = if (Test-Path -LiteralPath $lastMessagePath) { Get-Content -LiteralPath $lastMessagePath -Raw -Encoding UTF8 } else { '' }
    $probe.marker_found = ($probeResult.Output + "`n" + $lastMessage) -match 'RUNTIME_OK_中文'
    Add-KnownIssues ($probeResult.Output + "`n" + $probeResult.ErrorText) $issues

    if ($probeResult.TimedOut) {
        [void]$issues.Add((Issue fatal LIVE_PROBE_TIMEOUT 'The real read-only codex exec probe timed out.' 'Inspect the model/cache evidence in this report.'))
    } elseif ($probeResult.ExitCode -ne 0) {
        [void]$issues.Add((Issue fatal LIVE_PROBE_FAILED 'The real read-only codex exec probe failed.' 'Inspect probe.output and probe.error_text.'))
    } elseif (-not $probe.marker_found) {
        [void]$issues.Add((Issue fatal UTF8_OR_RESPONSE_FAILED 'The exact Chinese probe marker was not returned.' 'Inspect output for encoding corruption or startup failure.'))
    }
} elseif ($SkipProbe) {
    [void]$issues.Add((Issue warning LIVE_PROBE_SKIPPED 'The real exec probe was skipped.' 'Run again without -SkipProbe before implementation.'))
}

$fatals = @($issues | Where-Object severity -eq 'fatal')
$ready = (-not $SkipProbe) -and $fatals.Count -eq 0 -and [bool]$probe.marker_found
$report = [ordered]@{
    schema_version=2
    checked_at=(Get-Date).ToString('o')
    ready=$ready
    repository=$repo
    launcher=$launcher
    version=$version
    exec_help=$help
    login_status=[ordered]@{ result=$login; advisory_only=$true }
    capabilities=$caps
    probe=$probe
    issues=@($issues)
}
[IO.File]::WriteAllText($reportPath, ($report | ConvertTo-Json -Depth 10), $utf8)

Write-Host ''
Write-Host ('Version: ' + (($version.Output -split "`r?`n" | Select-Object -First 1)))
Write-Host "Live probe marker: $($probe.marker_found)"
foreach ($item in $issues) {
    $color = if ($item.severity -eq 'fatal') { 'Red' } elseif ($item.severity -eq 'warning') { 'Yellow' } else { 'Gray' }
    Write-Host "[$($item.severity.ToUpperInvariant())] $($item.code): $($item.message)" -ForegroundColor $color
    if ($item.fix) { Write-Host "  Fix: $($item.fix)" }
}

if ($ready) {
    Write-Host '[PASS] Codex runtime is ready.' -ForegroundColor Green
    Write-Host "Report: $reportPath"
    exit 0
}
Write-Host '[FAIL] Runtime is not ready. Diagnose from captured evidence, not wrapper assumptions.' -ForegroundColor Red
Write-Host "Report: $reportPath"
exit 1
