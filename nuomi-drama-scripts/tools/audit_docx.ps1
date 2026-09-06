[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [int]$ExpectedEpisodes = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Read-ZipXml([System.IO.Compression.ZipArchive]$Zip, [string]$EntryName) {
    $entry = $Zip.GetEntry($EntryName)
    if ($null -eq $entry) {
        throw "Missing package part: $EntryName"
    }
    $reader = [System.IO.StreamReader]::new($entry.Open(), [System.Text.Encoding]::UTF8)
    try {
        return [System.Xml.XmlDocument]::new() | ForEach-Object {
            $_.LoadXml($reader.ReadToEnd())
            $_
        }
    }
    finally {
        $reader.Dispose()
    }
}

if (-not (Test-Path -LiteralPath $Path)) {
    throw "DOCX does not exist: $Path"
}

$fullPath = [System.IO.Path]::GetFullPath($Path)
$zip = [System.IO.Compression.ZipFile]::OpenRead($fullPath)
try {
    $required = @(
        '[Content_Types].xml',
        '_rels/.rels',
        'word/document.xml',
        'word/styles.xml',
        'word/settings.xml',
        'word/_rels/document.xml.rels'
    )
    foreach ($name in $required) {
        if ($null -eq $zip.GetEntry($name)) {
            throw "Missing required entry: $name"
        }
    }

    $document = Read-ZipXml $zip 'word/document.xml'
    $styles = Read-ZipXml $zip 'word/styles.xml'
    $ns = [System.Xml.XmlNamespaceManager]::new($document.NameTable)
    $ns.AddNamespace('w', 'http://schemas.openxmlformats.org/wordprocessingml/2006/main')

    $section = $document.SelectSingleNode('//w:sectPr', $ns)
    $page = $section.SelectSingleNode('w:pgSz', $ns)
    $margin = $section.SelectSingleNode('w:pgMar', $ns)
    $episodes = $document.SelectNodes('//w:p[w:pPr/w:pStyle[@w:val="EpisodeTitle"]]', $ns).Count
    $scenes = $document.SelectNodes('//w:p[w:pPr/w:pStyle[@w:val="SceneHeading"]]', $ns).Count
    $actions = $document.SelectNodes('//w:p[w:pPr/w:pStyle[@w:val="Action"]]', $ns).Count
    $dialogues = $document.SelectNodes('//w:p[w:pPr/w:pStyle[@w:val="Dialogue"]]', $ns).Count
    $sfx = $document.SelectNodes('//w:p[w:pPr/w:pStyle[@w:val="Sfx"]]', $ns).Count
    $characters = $document.SelectNodes('//w:p[w:pPr/w:pStyle[@w:val="Characters"]]', $ns).Count
    $duration = $document.SelectNodes('//w:p[w:pPr/w:pStyle[@w:val="Duration"]]', $ns).Count
    $styleIds = @('Normal', 'CoverTitle', 'CoverMeta', 'EpisodeTitle', 'Duration', 'SectionTitle', 'SceneHeading', 'Characters', 'Action', 'Dialogue', 'Sfx')
    $missingStyles = @($styleIds | Where-Object { $null -eq $styles.SelectSingleNode("//w:style[@w:styleId='$_']", $ns) })

    $failures = [System.Collections.Generic.List[string]]::new()
    if ($page.GetAttribute('w:w') -ne '11906' -or $page.GetAttribute('w:h') -ne '16838') { [void]$failures.Add('page size is not A4 twips') }
    if ($margin.GetAttribute('w:top') -ne '1440' -or $margin.GetAttribute('w:bottom') -ne '1440' -or $margin.GetAttribute('w:left') -ne '1800' -or $margin.GetAttribute('w:right') -ne '1800') { [void]$failures.Add('margins do not match reference profile') }
    if ($episodes -ne ($ExpectedEpisodes + 1)) { [void]$failures.Add("EpisodeTitle count is $episodes, expected $($ExpectedEpisodes + 1)") }
    if ($scenes -lt $ExpectedEpisodes) { [void]$failures.Add("SceneHeading count is $scenes, expected at least $ExpectedEpisodes") }
    if ($actions -lt $ExpectedEpisodes) { [void]$failures.Add('Action paragraphs are missing') }
    if ($dialogues -lt $ExpectedEpisodes) { [void]$failures.Add('Dialogue paragraphs are missing') }
    if ($characters -lt $ExpectedEpisodes) { [void]$failures.Add('Characters paragraphs are missing') }
    if ($sfx -lt 1) { [void]$failures.Add('Sfx paragraph style is missing') }
    if ($missingStyles.Count -gt 0) { [void]$failures.Add("Missing styles: $($missingStyles -join ', ')") }

    [pscustomobject]@{
        Path = $fullPath
        PackageEntries = $zip.Entries.Count
        PageWidthTwips = $page.GetAttribute('w:w')
        PageHeightTwips = $page.GetAttribute('w:h')
        EpisodeTitleCount = $episodes
        SceneHeadingCount = $scenes
        ActionCount = $actions
        DialogueCount = $dialogues
        SfxCount = $sfx
        DurationCount = $duration
        CharactersCount = $characters
        MissingStyles = ($missingStyles -join ', ')
        Status = if ($failures.Count -eq 0) { 'PASS' } else { 'FAIL' }
    } | Format-List

    if ($failures.Count -gt 0) {
        $failures | ForEach-Object { Write-Error $_ }
        exit 1
    }
}
finally {
    $zip.Dispose()
}
