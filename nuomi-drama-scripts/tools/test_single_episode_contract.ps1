[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [int]$EpisodeNumber,
    [Parameter(Mandatory = $true)]
    [string]$EpisodeTitle,
    [Parameter(Mandatory = $true)]
    [int]$DurationSeconds,
    [int]$ExpectedSceneCount = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.IO.Compression

function Read-ZipXml([System.IO.Compression.ZipArchive]$Zip, [string]$EntryName) {
    $entry = $Zip.GetEntry($EntryName)
    if ($null -eq $entry) { throw "Missing package part: $EntryName" }
    $reader = [System.IO.StreamReader]::new($entry.Open(), [System.Text.Encoding]::UTF8)
    try { return [xml]$reader.ReadToEnd() } finally { $reader.Dispose() }
}

function Get-Paragraphs([xml]$DocumentXml) {
    $ns = [System.Xml.XmlNamespaceManager]::new($DocumentXml.NameTable)
    $ns.AddNamespace('w', 'http://schemas.openxmlformats.org/wordprocessingml/2006/main')
    $result = @()
    foreach ($paragraph in $DocumentXml.SelectNodes('//w:body/w:p', $ns)) {
        $style = $paragraph.SelectSingleNode('./w:pPr/w:pStyle/@w:val', $ns)
        $text = (($paragraph.SelectNodes('.//w:t', $ns) | ForEach-Object { $_.'#text' }) -join '')
        $result += [pscustomobject]@{ Text = $text; Style = if ($null -eq $style) { '' } else { $style.Value } }
    }
    return $result
}

$resolved = (Resolve-Path -LiteralPath $Path).Path
$stream = [System.IO.FileStream]::new($resolved, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
$zip = [System.IO.Compression.ZipArchive]::new($stream, [System.IO.Compression.ZipArchiveMode]::Read, $false)
try { $paragraphs = Get-Paragraphs (Read-ZipXml $zip 'word/document.xml') } finally { $zip.Dispose(); $stream.Dispose() }

$failures = [System.Collections.Generic.List[string]]::new()
$expectedTitle = "第$EpisodeNumber`集 · $EpisodeTitle"
$expectedDuration = "时长：${DurationSeconds}s"
$titleMatches = @($paragraphs | Where-Object { $_.Text -eq $expectedTitle })
$durationMatches = @($paragraphs | Where-Object { $_.Text -eq $expectedDuration })
$sceneMatches = @($paragraphs | Where-Object { $_.Style -eq 'SceneHeading' -and $_.Text -match "^$EpisodeNumber-\d+\s+.+\s+(日|夜|清晨|早晨|上午|中午|下午|傍晚|黄昏|深夜)\s+(内|外)$" })
$characterMatches = @($paragraphs | Where-Object { $_.Style -eq 'Characters' -and $_.Text -match '^人物：\S+(\s+\S+)+$' })
$legacyTitles = @($paragraphs | Where-Object { $_.Text -match '^第\d+集：' })
$contentParagraphs = @($paragraphs | Where-Object { -not [string]::IsNullOrWhiteSpace($_.Text) })

if ($titleMatches.Count -ne 1) { [void]$failures.Add("Episode title mismatch: expected exactly 1 '$expectedTitle', got $($titleMatches.Count)") }
if ($durationMatches.Count -ne 1) { [void]$failures.Add("Duration line mismatch: expected exactly 1 '$expectedDuration', got $($durationMatches.Count)") }
if ($sceneMatches.Count -ne $ExpectedSceneCount) { [void]$failures.Add("Scene heading mismatch: expected $ExpectedSceneCount numbered headings, got $($sceneMatches.Count)") }
if ($characterMatches.Count -ne $ExpectedSceneCount) { [void]$failures.Add("Characters line mismatch: expected $ExpectedSceneCount character lines, got $($characterMatches.Count)") }
if ($legacyTitles.Count -gt 0) { [void]$failures.Add("Legacy episode title format remains: $($legacyTitles.Count) paragraph(s)") }
if ($contentParagraphs.Count -lt 2 -or $contentParagraphs[0].Text -ne $expectedTitle -or $contentParagraphs[1].Text -ne $expectedDuration) {
    [void]$failures.Add('Single-episode document must begin with title followed immediately by duration')
}
if (@($paragraphs | Where-Object { $_.Style -in @('CoverTitle', 'CoverMeta', 'SectionTitle') }).Count -gt 0) {
    [void]$failures.Add('Single-episode document contains cover or project-preface paragraphs')
}
for ($index = 0; $index -lt $paragraphs.Count; $index++) {
    if ($paragraphs[$index].Style -eq 'SceneHeading') {
        if ($index + 1 -ge $paragraphs.Count -or $paragraphs[$index + 1].Style -ne 'Characters') {
            [void]$failures.Add("Scene heading at paragraph $index is not followed by a Characters line")
        }
    }
}

[pscustomobject]@{
    Path = $resolved
    ParagraphCount = $paragraphs.Count
    TitleCount = $titleMatches.Count
    DurationCount = $durationMatches.Count
    SceneHeadingCount = $sceneMatches.Count
    CharactersCount = $characterMatches.Count
    LegacyTitleCount = $legacyTitles.Count
    Status = if ($failures.Count -eq 0) { 'PASS' } else { 'FAIL' }
}
if ($failures.Count -gt 0) { $failures | ForEach-Object { Write-Error $_ } }
