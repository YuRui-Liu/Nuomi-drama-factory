[CmdletBinding()]
param(
    [string]$Repository,
    [string]$PromptFile,
    [int]$TimeoutMinutes = 120,
    [int]$ExitGraceSeconds = 8
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Repository) { $Repository = Split-Path -Parent $scriptDir }
if (-not $PromptFile) { $PromptFile = Join-Path $Repository 'docs\plans\2026-08-30-current-main-cli-executor-prompt.md' }

if (-not (Test-Path -LiteralPath $Repository -PathType Container)) { throw "Repository not found: $Repository" }
if (-not (Test-Path -LiteralPath $PromptFile -PathType Leaf)) { throw "Prompt file not found: $PromptFile" }

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

function Quote-CmdArgument([string]$Value) {
    if ($Value -notmatch '[\s"]') { return $Value }
    '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

function Stop-ProcessTree([int]$ProcessId) {
    & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
}

$launcher = Find-Codex
if (-not $launcher) { throw 'Codex CLI was not found on PATH.' }

$helpText = @(& $launcher exec --help 2>&1 | ForEach-Object { [string]$_ }) -join "`n"
if ($LASTEXITCODE -ne 0) { throw "codex exec --help failed:`n$helpText" }
if ($helpText -notmatch '(?m)^\s*(?:-\w,\s*)?--output-last-message\b') {
    throw 'This Codex CLI does not support --output-last-message; supervised completion cannot be proven safely.'
}

$runDir = Join-Path $Repository '.codex-cli-runs'
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$jsonLog = Join-Path $runDir "current-main-supervised-$stamp.jsonl"
$stderrLog = Join-Path $runDir "current-main-supervised-$stamp.stderr.log"
$finalNote = Join-Path $runDir "current-main-supervised-$stamp-final.md"
$runtimeResult = Join-Path $runDir "current-main-supervised-$stamp-runtime.json"

$arguments = New-Object System.Collections.Generic.List[string]
$arguments.Add('exec')
if ($helpText -match '(?m)^\s*(?:-\w,\s*)?--sandbox\b') {
    $arguments.Add('--sandbox')
    $arguments.Add('workspace-write')
} elseif ($helpText -match '(?m)^\s*(?:-\w,\s*)?--full-auto\b') {
    $arguments.Add('--full-auto')
}
if ($helpText -match '(?m)^\s*(?:-\w,\s*)?--json\b') { $arguments.Add('--json') }
$arguments.Add('--output-last-message')
$arguments.Add($finalNote)
if ($helpText -match '(?m)^\s*(?:-\w,\s*)?--skip-git-repo-check\b') { $arguments.Add('--skip-git-repo-check') }
$arguments.Add('-')

$prompt = Get-Content -LiteralPath $PromptFile -Raw -Encoding UTF8
$psi = New-Object System.Diagnostics.ProcessStartInfo
$extension = [IO.Path]::GetExtension($launcher).ToLowerInvariant()
if ($extension -eq '.cmd') {
    $psi.FileName = $env:ComSpec
    $cliCommand = (Quote-CmdArgument $launcher) + ' ' + (($arguments | ForEach-Object { Quote-CmdArgument $_ }) -join ' ')
    $psi.Arguments = '/d /s /c "' + $cliCommand + '"'
} elseif ($extension -eq '.ps1') {
    $psi.FileName = 'powershell.exe'
    $allArguments = @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',$launcher) + @($arguments)
    $psi.Arguments = ($allArguments | ForEach-Object { Quote-CmdArgument $_ }) -join ' '
} else {
    $psi.FileName = $launcher
    $psi.Arguments = ($arguments | ForEach-Object { Quote-CmdArgument $_ }) -join ' '
}
$psi.WorkingDirectory = $Repository
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardInput = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
try { $psi.StandardOutputEncoding = $utf8; $psi.StandardErrorEncoding = $utf8 } catch {}

$process = New-Object System.Diagnostics.Process
$process.StartInfo = $psi
if (-not $process.Start()) { throw 'Failed to start the Codex process.' }
$stdoutTask = $process.StandardOutput.ReadToEndAsync()
$stderrTask = $process.StandardError.ReadToEndAsync()
$process.StandardInput.Write($prompt)
$process.StandardInput.Close()

Write-Host 'Nuomi Codex Supervised Runner'
Write-Host "Repository: $Repository"
Write-Host "Codex:     $launcher"
Write-Host "Prompt:    $PromptFile"
Write-Host "PID:       $($process.Id)"
Write-Host "Final:     $finalNote"
Write-Host "Timeout:   $TimeoutMinutes minutes"

$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$finalStableAt = $null
$finalSignature = $null
$completedByFinalMessage = $false
$timedOut = $false

while ($true) {
    if ($process.HasExited) { break }

    if (Test-Path -LiteralPath $finalNote) {
        $info = Get-Item -LiteralPath $finalNote
        if ($info.Length -gt 0) {
            $signature = "$($info.Length):$($info.LastWriteTimeUtc.Ticks)"
            if ($signature -ne $finalSignature) {
                $finalSignature = $signature
                $finalStableAt = Get-Date
                Write-Host 'Final message detected; waiting for file stability and normal CLI exit...'
            } elseif ($null -ne $finalStableAt -and ((Get-Date) - $finalStableAt).TotalSeconds -ge $ExitGraceSeconds) {
                $completedByFinalMessage = $true
                Write-Host 'Final message is stable; terminating only the lingering Codex process tree.' -ForegroundColor Yellow
                Stop-ProcessTree $process.Id
                break
            }
        }
    }

    if ((Get-Date) -ge $deadline) {
        $timedOut = $true
        Write-Host 'Execution timeout reached; terminating the process tree.' -ForegroundColor Red
        Stop-ProcessTree $process.Id
        break
    }
    Start-Sleep -Milliseconds 500
}

try { $process.WaitForExit(10000) | Out-Null } catch {}
$stdout = $stdoutTask.Result
$stderr = $stderrTask.Result
[IO.File]::WriteAllText($jsonLog, $stdout, $utf8)
[IO.File]::WriteAllText($stderrLog, $stderr, $utf8)

$finalText = if (Test-Path -LiteralPath $finalNote) { Get-Content -LiteralPath $finalNote -Raw -Encoding UTF8 } else { '' }
$success = (-not $timedOut) -and (-not [string]::IsNullOrWhiteSpace($finalText))
$result = [ordered]@{
    schema_version = 1
    finished_at = (Get-Date).ToString('o')
    success = $success
    completed_by_final_message = $completedByFinalMessage
    timed_out = $timedOut
    process_exit_code = if ($process.HasExited) { $process.ExitCode } else { $null }
    repository = $Repository
    prompt_file = $PromptFile
    final_message = $finalNote
    stdout_log = $jsonLog
    stderr_log = $stderrLog
}
[IO.File]::WriteAllText($runtimeResult, ($result | ConvertTo-Json -Depth 6), $utf8)

if ($success) {
    Write-Host '[PASS] Codex produced a trustworthy final response.' -ForegroundColor Green
    Write-Host "Result: $runtimeResult"
    exit 0
}
Write-Host '[FAIL] No trustworthy final response was produced.' -ForegroundColor Red
Write-Host "Result: $runtimeResult"
exit 1
