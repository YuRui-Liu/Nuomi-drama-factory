[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [int]$EpisodeCount = 30,
    [int]$EpisodeNumber = 0,
    [string]$ProjectName = '格式验收稿',
    [switch]$SmokeTest,
    [switch]$SingleEpisode,
    [string]$ManuscriptDir = '',
    [switch]$RequireDeliveryGate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($EpisodeCount -lt 1 -or $EpisodeCount -gt 200) {
    throw 'EpisodeCount must be between 1 and 200.'
}
if ($SingleEpisode -and ($EpisodeNumber -lt 1 -or $EpisodeNumber -gt 200)) {
    throw 'SingleEpisode requires EpisodeNumber between 1 and 200.'
}
if (-not $SingleEpisode -and $EpisodeNumber -ne 0) {
    throw 'EpisodeNumber can only be used with SingleEpisode.'
}
if ($SingleEpisode) {
    $EpisodeCount = 1
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Escape-Xml([AllowNull()][string]$Value) {
    if ($null -eq $Value) {
        return ''
    }
    return [System.Security.SecurityElement]::Escape($Value)
}

function New-RunXml(
    [AllowNull()][string]$Text,
    [string]$Role = 'Body',
    [switch]$Bold
) {
    $escaped = Escape-Xml $Text
    $color = '000000'
    $font = 'SimSun'
    $size = '21'
    $boldXml = if ($Bold) { '<w:b/>' } else { '' }

    switch ($Role) {
        'CoverTitle' { $font = 'SimHei'; $size = '64'; $boldXml = '<w:b/>' }
        'CoverMeta' { $font = 'SimSun'; $size = '21'; $color = '666666' }
        'EpisodeTitle' { $font = 'SimHei'; $size = '32'; $boldXml = '<w:b/>' }
        'Duration' { $font = 'SimSun'; $size = '21'; $color = '666666' }
        'SectionTitle' { $font = 'SimHei'; $size = '24'; $color = '6B4A2F'; $boldXml = '<w:b/>' }
        'SceneHeading' { $font = 'SimHei'; $size = '23'; $color = '2A2A2A'; $boldXml = '<w:b/>' }
        'Characters' { $font = 'SimSun'; $size = '21'; $color = '555555' }
        'Action' { $font = 'SimSun'; $size = '21' }
        'Dialogue' { $font = 'SimSun'; $size = '21'; $color = '1F4EC0' }
        'Sfx' { $font = 'SimSun'; $size = '21'; $color = 'C01B1B' }
    }

    return "<w:r><w:rPr><w:rFonts w:ascii='$font' w:eastAsia='宋体' w:hAnsi='$font'/><w:color w:val='$color'/><w:sz w:val='$size'/><w:szCs w:val='$size'/>$boldXml</w:rPr><w:t xml:space='preserve'>$escaped</w:t></w:r>"
}

function New-ParagraphXml(
    [AllowNull()][string]$Text,
    [string]$Role = 'Body',
    [switch]$PageBreakBefore,
    [int]$Before = 0,
    [int]$After = 100,
    [int]$Line = 360
) {
    $style = 'Normal'
    $alignment = 'left'

    switch ($Role) {
        'CoverTitle' { $style = 'CoverTitle'; $alignment = 'center'; $Before = 0; $After = 240; $Line = 480 }
        'CoverMeta' { $style = 'CoverMeta'; $alignment = 'center'; $Before = 0; $After = 80; $Line = 300 }
        'EpisodeTitle' { $style = 'EpisodeTitle'; $alignment = 'center'; $Before = 240; $After = 240; $Line = 360 }
        'Duration' { $style = 'Duration'; $alignment = 'left'; $Before = 0; $After = 180; $Line = 300 }
        'SectionTitle' { $style = 'SectionTitle'; $alignment = 'left'; $Before = 180; $After = 100; $Line = 360 }
        'SceneHeading' { $style = 'SceneHeading'; $alignment = 'left'; $Before = 160; $After = 80; $Line = 300 }
        'Characters' { $style = 'Characters'; $alignment = 'left'; $Before = 0; $After = 80; $Line = 300 }
        'Action' { $style = 'Action'; $alignment = 'left'; $Before = 0; $After = 100; $Line = 360 }
        'Dialogue' { $style = 'Dialogue'; $alignment = 'left'; $Before = 0; $After = 100; $Line = 360 }
        'Sfx' { $style = 'Sfx'; $alignment = 'left'; $Before = 0; $After = 100; $Line = 360 }
    }

    $breakXml = if ($PageBreakBefore) { '<w:pageBreakBefore/>' } else { '' }
    $pPr = "<w:pPr><w:pStyle w:val='$style'/><w:jc w:val='$alignment'/><w:spacing w:before='$Before' w:after='$After' w:line='$Line' w:lineRule='auto'/>$breakXml</w:pPr>"
    return "<w:p>$pPr$(New-RunXml -Text $Text -Role $Role)</w:p>"
}

function Get-YamlScalar([string]$YamlPath, [string]$Key, [string]$DefaultValue) {
    if (-not (Test-Path -LiteralPath $YamlPath)) {
        return $DefaultValue
    }
    $text = [System.IO.File]::ReadAllText($YamlPath, [System.Text.UTF8Encoding]::new($false))
    $pattern = '(?m)^' + [regex]::Escape($Key) + '\s*:\s*["'']?(.*?)["'']?\s*$'
    $match = [regex]::Match($text, $pattern)
    if ($match.Success -and $match.Groups[1].Value.Trim().Length -gt 0) {
        return $match.Groups[1].Value.Trim()
    }
    return $DefaultValue
}

function Add-MarkdownFileToDocument(
    [System.Collections.Generic.List[string]]$Paragraphs,
    [string]$FilePath,
    [switch]$SkipEpisodeHeading,
    [switch]$EpisodeMode
) {
    if (-not (Test-Path -LiteralPath $FilePath)) {
        return
    }
    $lines = [System.IO.File]::ReadAllText($FilePath, [System.Text.UTF8Encoding]::new($false)) -split "\r?\n"
    $frontMatter = $false
    for ($lineIndex = 0; $lineIndex -lt $lines.Count; $lineIndex++) {
        $rawLine = $lines[$lineIndex]
        $line = $rawLine.Trim()
        if ($line -eq '---') {
            $frontMatter = -not $frontMatter
            continue
        }
        if ($frontMatter -or [string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        if ($EpisodeMode -and $line -match '^时长：') {
            continue
        }
        if ($SkipEpisodeHeading -and $line -match '^#\s*(第\d+\s*集|E\d{3})') {
            continue
        }
        if ($line -match '^#{1,3}\s*(.+)$') {
            $heading = $matches[1].Trim()
            if (($EpisodeMode -and $line -match '^###\s') -or $heading -match '^\d+-\d+\s+' -or $heading -match '·[^·]+·' -or $heading -match '^(INT|EXT)[\.、\s]') {
                $Paragraphs.Add((New-ParagraphXml -Text $heading -Role 'SceneHeading'))
                if ($EpisodeMode) {
                    $nextNonEmptyLine = ''
                    for ($scanIndex = $lineIndex + 1; $scanIndex -lt $lines.Count; $scanIndex++) {
                        $candidateLine = $lines[$scanIndex].Trim()
                        if (-not [string]::IsNullOrWhiteSpace($candidateLine)) {
                            $nextNonEmptyLine = $candidateLine
                            break
                        }
                    }
                    if ($nextNonEmptyLine -notmatch '^人物：') {
                        $inferredNames = [System.Collections.Generic.List[string]]::new()
                        for ($scanIndex = $lineIndex + 1; $scanIndex -lt $lines.Count; $scanIndex++) {
                            $candidateLine = $lines[$scanIndex].Trim()
                            if ($candidateLine -match '^#{1,3}\s') { break }
                            if ($candidateLine -match '^([^：:]{1,24})[:：]\s*.+$') {
                                $candidateName = $matches[1].Trim()
                                if ($candidateLine -notmatch '^(时长|人物|纸条|苏岚字迹|苏岚录音)：' -and -not $inferredNames.Contains($candidateName)) {
                                    [void]$inferredNames.Add($candidateName)
                                }
                            }
                        }
                        if ($inferredNames.Count -gt 0) {
                            $Paragraphs.Add((New-ParagraphXml -Text ("人物：" + ($inferredNames -join ' ')) -Role 'Characters'))
                        }
                    }
                }
            }
            else {
                $Paragraphs.Add((New-ParagraphXml -Text $heading -Role 'SectionTitle'))
            }
            continue
        }
        if ($line.StartsWith('△')) {
            $Paragraphs.Add((New-ParagraphXml -Text $line -Role 'Action'))
            continue
        }
        if ($line.StartsWith('【')) {
            $Paragraphs.Add((New-ParagraphXml -Text $line -Role 'Sfx'))
            continue
        }
        if ($EpisodeMode -and $line -match '^人物：\S+(\s+\S+)+$') {
            $Paragraphs.Add((New-ParagraphXml -Text $line -Role 'Characters'))
            continue
        }
        if ($line -match '^[^：:]{1,24}[:：]\s*.+$') {
            $Paragraphs.Add((New-ParagraphXml -Text $line -Role 'Dialogue'))
            continue
        }
        if ($line -match '^(episode|title|volume|status|characters|locations|timeline|main_thread_progress|character_thread_progress|emotion_or_mystery_progress|hook|body_char_count|rights_risk)\s*:') {
            continue
        }
        $Paragraphs.Add((New-ParagraphXml -Text $line -Role 'Action'))
    }
}

function New-StylesXml {
    $xml = @'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault><w:rPr><w:rFonts w:ascii="SimSun" w:eastAsia="宋体" w:hAnsi="SimSun"/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:rPrDefault>
    <w:pPrDefault><w:pPr><w:spacing w:after="100" w:line="360" w:lineRule="auto"/></w:pPr></w:pPrDefault>
  </w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/><w:rPr><w:rFonts w:ascii="SimSun" w:eastAsia="宋体" w:hAnsi="SimSun"/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="CoverTitle"><w:name w:val="CoverTitle"/><w:basedOn w:val="Normal"/><w:uiPriority w:val="1"/><w:qFormat/><w:pPr><w:jc w:val="center"/></w:pPr><w:rPr><w:rFonts w:ascii="SimHei" w:eastAsia="黑体" w:hAnsi="SimHei"/><w:b/><w:sz w:val="64"/><w:szCs w:val="64"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="CoverMeta"><w:name w:val="CoverMeta"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/></w:pPr><w:rPr><w:color w:val="666666"/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="EpisodeTitle"><w:name w:val="EpisodeTitle"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/></w:pPr><w:rPr><w:rFonts w:ascii="SimHei" w:eastAsia="黑体" w:hAnsi="SimHei"/><w:b/><w:sz w:val="32"/><w:szCs w:val="32"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Duration"><w:name w:val="Duration"/><w:basedOn w:val="Normal"/><w:rPr><w:color w:val="666666"/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="SectionTitle"><w:name w:val="SectionTitle"/><w:basedOn w:val="Normal"/><w:rPr><w:rFonts w:ascii="SimHei" w:eastAsia="黑体" w:hAnsi="SimHei"/><w:b/><w:color w:val="6B4A2F"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="SceneHeading"><w:name w:val="SceneHeading"/><w:basedOn w:val="Normal"/><w:rPr><w:rFonts w:ascii="SimHei" w:eastAsia="黑体" w:hAnsi="SimHei"/><w:b/><w:color w:val="2A2A2A"/><w:sz w:val="23"/><w:szCs w:val="23"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Characters"><w:name w:val="Characters"/><w:basedOn w:val="Normal"/><w:rPr><w:color w:val="555555"/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Action"><w:name w:val="Action"/><w:basedOn w:val="Normal"/></w:style>
  <w:style w:type="paragraph" w:styleId="Dialogue"><w:name w:val="Dialogue"/><w:basedOn w:val="Normal"/><w:rPr><w:color w:val="1F4EC0"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Sfx"><w:name w:val="Sfx"/><w:basedOn w:val="Normal"/><w:rPr><w:color w:val="C01B1B"/></w:rPr></w:style>
</w:styles>
'@
    return $xml
}

function New-DocumentXml {
    param(
        [string]$Name,
        [int]$Count,
        [switch]$IsSmokeTest,
        [string]$ManuscriptDir = '',
        [int]$StartEpisode = 1,
        [int]$DefaultDurationSeconds = 90,
        [switch]$SingleEpisode
    )

    $paragraphs = [System.Collections.Generic.List[string]]::new()
    if (-not $SingleEpisode) {
    $paragraphs.Add((New-ParagraphXml -Text $Name -Role 'CoverTitle'))
    $paragraphs.Add((New-ParagraphXml -Text '女频 · 现实共鸣 · 格式验收样稿' -Role 'CoverMeta'))
    $paragraphs.Add((New-ParagraphXml -Text "首交 $Count 集 · 红果漫剧 DOCX 结构验收" -Role 'CoverMeta'))
    $paragraphs.Add((New-ParagraphXml -Text 'A4 · 9:16 竖屏内容的剧本交付格式画像' -Role 'CoverMeta'))
    $paragraphs.Add((New-ParagraphXml -Text '版本：v0.1 · 内容为格式测试样例，不是投稿正文' -Role 'CoverMeta'))
    $paragraphs.Add((New-ParagraphXml -Text '交付状态：delivery_blocked（待渲染环境恢复）' -Role 'CoverMeta'))
    $paragraphs.Add('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    $paragraphs.Add((New-ParagraphXml -Text '第零集' -Role 'EpisodeTitle'))
    $paragraphs.Add((New-ParagraphXml -Text '——《人物小传与故事大纲》——' -Role 'SectionTitle'))
    if ([string]::IsNullOrWhiteSpace($ManuscriptDir)) {
        $paragraphs.Add((New-ParagraphXml -Text '一、格式验收说明' -Role 'SectionTitle'))
        $paragraphs.Add((New-ParagraphXml -Text '本页用于检查封面后的前置结构。真实项目将在此处放置人物小传、故事简介、故事大纲和版权状态。' -Role 'Action'))
        $paragraphs.Add((New-ParagraphXml -Text '二、人物小传（测试）' -Role 'SectionTitle'))
        $paragraphs.Add((New-ParagraphXml -Text '主角：待真实项目填写。当前内容仅用于验证正文段落、字体和分页。' -Role 'Action'))
        $paragraphs.Add((New-ParagraphXml -Text '三、故事大纲（测试）' -Role 'SectionTitle'))
        $paragraphs.Add((New-ParagraphXml -Text '故事大纲：待真实项目填写。当前内容不能作为投稿内容。' -Role 'Action'))
    }
    else {
        $paragraphs.Add((New-ParagraphXml -Text '一、项目简报' -Role 'SectionTitle'))
        Add-MarkdownFileToDocument -Paragraphs $paragraphs -FilePath (Join-Path $ManuscriptDir '00_项目简报.md')
        $paragraphs.Add((New-ParagraphXml -Text '二、人物小传与剧本圣经' -Role 'SectionTitle'))
        Add-MarkdownFileToDocument -Paragraphs $paragraphs -FilePath (Join-Path $ManuscriptDir '04_人物表.md')
        Add-MarkdownFileToDocument -Paragraphs $paragraphs -FilePath (Join-Path $ManuscriptDir '03_剧本圣经.md')
        $paragraphs.Add((New-ParagraphXml -Text '三、故事简介与大纲' -Role 'SectionTitle'))
        Add-MarkdownFileToDocument -Paragraphs $paragraphs -FilePath (Join-Path $ManuscriptDir '02_故事大纲.md')
    }
    }

    for ($episode = $StartEpisode; $episode -lt ($StartEpisode + $Count); $episode++) {
        if (-not [string]::IsNullOrWhiteSpace($ManuscriptDir)) {
            $episodePath = Join-Path $ManuscriptDir ('episodes\E{0:D3}.md' -f $episode)
            $episodeTitle = Get-YamlScalar -YamlPath $episodePath -Key 'title' -DefaultValue "第$episode 集"
            $durationText = Get-YamlScalar -YamlPath $episodePath -Key 'duration_seconds' -DefaultValue ''
            $durationSeconds = $DefaultDurationSeconds
            $parsedDuration = 0
            if ([int]::TryParse($durationText, [ref]$parsedDuration) -and $parsedDuration -gt 0) {
                $durationSeconds = $parsedDuration
            }
            $title = "第${episode}集 · $episodeTitle"
        }
        else {
            $durationSeconds = $DefaultDurationSeconds
            $title = "第${episode}集 · 格式验收样例"
        }
        $pageBreakBefore = -not $SingleEpisode
        $paragraphs.Add((New-ParagraphXml -Text $title -Role 'EpisodeTitle' -PageBreakBefore:$pageBreakBefore))
        $paragraphs.Add((New-ParagraphXml -Text "时长：${durationSeconds}s" -Role 'Duration'))
        if (-not [string]::IsNullOrWhiteSpace($ManuscriptDir)) {
            Add-MarkdownFileToDocument -Paragraphs $paragraphs -FilePath $episodePath -SkipEpisodeHeading -EpisodeMode
        }
        else {
            $scene = "$episode-1 测试场景（内） 日"
            $paragraphs.Add((New-ParagraphXml -Text $scene -Role 'SceneHeading'))
            $paragraphs.Add((New-ParagraphXml -Text '人物：主角' -Role 'Characters'))
            $paragraphs.Add((New-ParagraphXml -Text '△ 这是格式验收动作行：检查场景标题、动作段落和段间距。真实项目将由 manuscript/episodes/ 文件生成。' -Role 'Action'))
            $paragraphs.Add((New-ParagraphXml -Text '△ 主角拿起一件登记在剧本圣经中的道具，冲突在本场景中产生具体推进。' -Role 'Action'))
            $paragraphs.Add((New-ParagraphXml -Text '主角：（测试对白）这句文字用于检查对白颜色、角色名和中文字体。' -Role 'Dialogue'))
            $paragraphs.Add((New-ParagraphXml -Text '【音效】门锁轻响。' -Role 'Sfx'))
            $paragraphs.Add((New-ParagraphXml -Text "△ 第$episode 集结尾形成测试钩子；真实项目必须替换为具体的不可逆事件。" -Role 'Action'))
        }
    }

    $body = ($paragraphs -join '')
    $xml = @(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '  <w:body>'
        $body
        '    <w:sectPr>'
        '      <w:pgSz w:w="11906" w:h="16838"/>'
        '      <w:pgMar w:top="1440" w:right="1800" w:bottom="1440" w:left="1800" w:header="720" w:footer="720" w:gutter="0"/>'
        '      <w:cols w:num="1"/>'
        '      <w:docGrid w:linePitch="360"/>'
        '    </w:sectPr>'
        '  </w:body>'
        '</w:document>'
    ) -join [Environment]::NewLine
    return $xml
}

function New-SettingsXml {
    $xml = @'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:updateFields w:val="true"/>
  <w:compat/>
</w:settings>
'@
    return $xml
}

function New-ContentTypesXml {
    $xml = @'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
</Types>
'@
    return $xml
}

function New-RootRelationshipsXml {
    $xml = @'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
'@
    return $xml
}

function New-DocumentRelationshipsXml {
    $xml = @'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
</Relationships>
'@
    return $xml
}

function Assert-DeliveryGate([string]$ResolvedManuscriptDir) {
    $projectRoot = Split-Path -Parent $ResolvedManuscriptDir
    $projectYaml = Join-Path $projectRoot 'project.yaml'
    $reviewsDir = Join-Path $projectRoot 'reviews'
    $qaDir = Join-Path (Join-Path $projectRoot 'deliverables') 'qa'
    if (-not (Test-Path -LiteralPath $projectYaml -PathType Leaf)) { throw 'Delivery gate blocked: project.yaml is missing.' }
    if (-not (Test-Path -LiteralPath $reviewsDir -PathType Container)) { throw 'Delivery gate blocked: reviews directory is missing.' }
    if (-not (Test-Path -LiteralPath $qaDir -PathType Container)) { throw 'Delivery gate blocked: rendered QA directory is missing.' }
    if (@(Get-ChildItem -LiteralPath $qaDir -File -Recurse).Count -eq 0) { throw 'Delivery gate blocked: rendered QA evidence is missing.' }

    $yaml = [System.IO.File]::ReadAllText($projectYaml, [System.Text.UTF8Encoding]::new($false))
    foreach ($blockedStatus in @('pending', 'blocked', 'rights_blocked', 'needs_review', 'draft', 'draft-for-platform-test', 'blocked-by-render-environment')) {
        $statusPattern = '(?im)^\s*(original_or_authorized|adaptation_license|image_voice_brand_clearance|ai_assistance_disclosure)\s*:\s*["'']?{0}["'']?\s*$' -f [regex]::Escape($blockedStatus)
        if ($yaml -match $statusPattern) {
            throw "Delivery gate blocked: project rights/AI status is $blockedStatus."
        }
        $deliveryPattern = '(?im)^\s*(status|visual_qa)\s*:\s*["'']?{0}["'']?\s*$' -f [regex]::Escape($blockedStatus)
        if ($yaml -match $deliveryPattern) {
            throw "Delivery gate blocked: delivery status is $blockedStatus."
        }
    }
    foreach ($reviewFile in Get-ChildItem -LiteralPath $reviewsDir -File -Recurse) {
        $reviewText = [System.IO.File]::ReadAllText($reviewFile.FullName, [System.Text.UTF8Encoding]::new($false))
        if ($reviewText -match '(?im)^\s*(status|delivery_status|rights_status)\s*:\s*(P0|P1|blocked|rights_blocked|needs_review|pending)\s*$') {
            throw "Delivery gate blocked by review: $($reviewFile.FullName)"
        }
    }
}

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$parent = Split-Path -Parent $resolvedOutput
if (-not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
}
if (Test-Path -LiteralPath $resolvedOutput) {
    throw "Output already exists: $resolvedOutput"
}

$resolvedManuscriptDir = ''
$startEpisode = if ($SingleEpisode) { $EpisodeNumber } else { 1 }
$defaultDurationSeconds = 90
if (-not [string]::IsNullOrWhiteSpace($ManuscriptDir)) {
    $resolvedManuscriptDir = [System.IO.Path]::GetFullPath($ManuscriptDir)
    if (-not (Test-Path -LiteralPath $resolvedManuscriptDir -PathType Container)) {
        throw "Manuscript directory does not exist: $resolvedManuscriptDir"
    }
    for ($episode = $startEpisode; $episode -lt ($startEpisode + $EpisodeCount); $episode++) {
        $episodePath = Join-Path $resolvedManuscriptDir ('episodes\E{0:D3}.md' -f $episode)
        if (-not (Test-Path -LiteralPath $episodePath -PathType Leaf)) {
            throw "Missing episode source: $episodePath"
        }
        if ($SingleEpisode) {
            $singleDurationText = Get-YamlScalar -YamlPath $episodePath -Key 'duration_seconds' -DefaultValue ''
            $singleDurationValue = 0
            if (-not [int]::TryParse($singleDurationText, [ref]$singleDurationValue) -or $singleDurationValue -le 0) {
                throw "Single episode source must declare a positive duration_seconds: $episodePath"
            }
        }
    }
    if ($ProjectName -eq '格式验收稿') {
        $projectYamlPath = Join-Path $resolvedManuscriptDir '..\project.yaml'
        $ProjectName = Get-YamlScalar -YamlPath $projectYamlPath -Key 'project_name' -DefaultValue ''
        if ([string]::IsNullOrWhiteSpace($ProjectName)) {
            $ProjectName = Get-YamlScalar -YamlPath $projectYamlPath -Key 'title' -DefaultValue '格式验收稿'
        }
        $projectDuration = Get-YamlScalar -YamlPath $projectYamlPath -Key 'episode_duration_seconds' -DefaultValue ''
        $parsedProjectDuration = 0
        if ([int]::TryParse($projectDuration, [ref]$parsedProjectDuration) -and $parsedProjectDuration -gt 0) {
            $defaultDurationSeconds = $parsedProjectDuration
        }
    }
}
if ($RequireDeliveryGate) {
    if ([string]::IsNullOrWhiteSpace($resolvedManuscriptDir)) { throw 'RequireDeliveryGate requires ManuscriptDir.' }
    Assert-DeliveryGate -ResolvedManuscriptDir $resolvedManuscriptDir
}

$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("nuomi-script-docx-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path (Join-Path $staging '_rels'), (Join-Path $staging 'word'), (Join-Path $staging 'word\_rels') | Out-Null
$utf8 = [System.Text.UTF8Encoding]::new($false)

try {
    [System.IO.File]::WriteAllText((Join-Path $staging '[Content_Types].xml'), (New-ContentTypesXml), $utf8)
    [System.IO.File]::WriteAllText((Join-Path $staging '_rels\.rels'), (New-RootRelationshipsXml), $utf8)
    [System.IO.File]::WriteAllText((Join-Path $staging 'word\document.xml'), (New-DocumentXml -Name $ProjectName -Count $EpisodeCount -IsSmokeTest:$SmokeTest -ManuscriptDir $resolvedManuscriptDir -StartEpisode $startEpisode -DefaultDurationSeconds $defaultDurationSeconds -SingleEpisode:$SingleEpisode), $utf8)
    [System.IO.File]::WriteAllText((Join-Path $staging 'word\styles.xml'), (New-StylesXml), $utf8)
    [System.IO.File]::WriteAllText((Join-Path $staging 'word\settings.xml'), (New-SettingsXml), $utf8)
    [System.IO.File]::WriteAllText((Join-Path $staging 'word\_rels\document.xml.rels'), (New-DocumentRelationshipsXml), $utf8)

    $zip = [System.IO.Compression.ZipFile]::Open($resolvedOutput, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        Get-ChildItem -LiteralPath $staging -Recurse -File | ForEach-Object {
            $relative = $_.FullName.Substring($staging.Length + 1).Replace('\', '/')
            $entry = $zip.CreateEntry($relative, [System.IO.Compression.CompressionLevel]::Optimal)
            $source = [System.IO.File]::OpenRead($_.FullName)
            $target = $entry.Open()
            try {
                $source.CopyTo($target)
            }
            finally {
                $target.Dispose()
                $source.Dispose()
            }
        }
    }
    finally {
        $zip.Dispose()
    }

    Write-Output "Created $resolvedOutput"
    if ($RequireDeliveryGate) {
        Write-Output 'Status ready'
    }
}
finally {
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force
    }
}
