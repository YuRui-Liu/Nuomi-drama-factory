[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [int]$EpisodeNumber
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$resolved = (Resolve-Path -LiteralPath $Path).Path
$text = [System.IO.File]::ReadAllText($resolved, [System.Text.UTF8Encoding]::new($false))
$lines = $text -split "\r?\n"
$failures = [System.Collections.Generic.List[string]]::new()

if ($text -notmatch '(?m)^#\s+(E\d{3}|\p{IsCJKUnifiedIdeographs}+\d+\s*\u96C6)') { [void]$failures.Add('missing episode title') }
if ($text -notmatch '(?m)^\p{IsCJKUnifiedIdeographs}+\uFF1A\d+s\s*$') { [void]$failures.Add('missing positive duration') }
if (@($lines | Where-Object { $_ -match '^#{2,3}\s+\S+' -or $_ -match "^$EpisodeNumber-\d+\s+\S+" }).Count -eq 0) { [void]$failures.Add('missing scene heading') }
if ($text -notmatch '(?m)^\p{IsCJKUnifiedIdeographs}+\uFF1A\S+(\s+\S+)+\s*$') { [void]$failures.Add('missing characters line') }
if ($text -notmatch '(?m)^\u25B3\S*') { [void]$failures.Add('missing visible action') }
if ($text -notmatch '(?m)^.+\uFF1A.+$') { [void]$failures.Add('missing dialogue or sound line') }
if ($text -match '(?i)image_prompt|video_prompt|provider|model_parameters') { [void]$failures.Add('media field found in episode source') }

[pscustomobject]@{
    Path = $resolved
    EpisodeNumber = $EpisodeNumber
    Status = if ($failures.Count -eq 0) { 'PASS' } else { 'FAIL' }
    FailureCount = $failures.Count
}
if ($failures.Count -gt 0) { $failures | ForEach-Object { Write-Error $_ } }
