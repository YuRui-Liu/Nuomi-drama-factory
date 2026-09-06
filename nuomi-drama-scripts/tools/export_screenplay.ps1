[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ManuscriptDir,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [int]$EpisodeStart = 1,
    [int]$EpisodeCount = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($EpisodeStart -lt 1) { throw 'EpisodeStart must be positive.' }
if ($EpisodeCount -lt 1 -or $EpisodeCount -gt 200) { throw 'EpisodeCount must be between 1 and 200.' }
$resolvedManuscript = [System.IO.Path]::GetFullPath($ManuscriptDir)
if (-not (Test-Path -LiteralPath $resolvedManuscript -PathType Container)) {
    throw "Manuscript directory does not exist: $resolvedManuscript"
}

function Get-ScreenplayBody {
    param([string]$Path)

    $lines = [System.IO.File]::ReadAllText($Path, [System.Text.UTF8Encoding]::new($false)) -split "\r?\n"
    $insideFrontMatter = $false
    $insideComment = $false
    $body = [System.Collections.Generic.List[string]]::new()
    foreach ($rawLine in $lines) {
        $line = $rawLine.TrimEnd()
        if ($line.Trim() -eq '---' -and ($body.Count -eq 0 -or $insideFrontMatter)) {
            $insideFrontMatter = -not $insideFrontMatter
            continue
        }
        if ($insideFrontMatter) { continue }
        if ($line.Trim().StartsWith('<!--')) { $insideComment = $true }
        if (-not $insideComment) { [void]$body.Add($line) }
        if ($line.Trim().EndsWith('-->')) { $insideComment = $false }
    }
    while ($body.Count -gt 0 -and [string]::IsNullOrWhiteSpace($body[0])) { $body.RemoveAt(0) }
    while ($body.Count -gt 0 -and [string]::IsNullOrWhiteSpace($body[$body.Count - 1])) { $body.RemoveAt($body.Count - 1) }
    if ($body.Count -eq 0) { throw "Episode body is empty: $Path" }
    return $body
}

$outputLines = [System.Collections.Generic.List[string]]::new()
for ($episode = $EpisodeStart; $episode -lt ($EpisodeStart + $EpisodeCount); $episode++) {
    $episodePath = Join-Path $resolvedManuscript ('episodes\E{0:D3}.md' -f $episode)
    if (-not (Test-Path -LiteralPath $episodePath -PathType Leaf)) {
        throw "Missing episode source: $episodePath"
    }
    $body = Get-ScreenplayBody -Path $episodePath
    foreach ($line in $body) {
        if ($line -match '(?i)image_prompt|video_prompt|provider|model_parameters') {
            throw "Media field found in screenplay source: $episodePath"
        }
        [void]$outputLines.Add($line)
    }
    if ($episode -lt ($EpisodeStart + $EpisodeCount - 1)) {
        [void]$outputLines.Add('')
        [void]$outputLines.Add('')
    }
}

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$parent = Split-Path -Parent $resolvedOutput
if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
if (Test-Path -LiteralPath $resolvedOutput) { throw "Output already exists: $resolvedOutput" }
[System.IO.File]::WriteAllText($resolvedOutput, ($outputLines -join [Environment]::NewLine) + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))

[pscustomobject]@{
    Status = 'PASS'
    OutputPath = $resolvedOutput
    EpisodeStart = $EpisodeStart
    EpisodeCount = $EpisodeCount
} | Format-List
