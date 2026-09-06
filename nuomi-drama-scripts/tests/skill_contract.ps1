[CmdletBinding()]
param(
    [string]$SkillRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

function Assert-Contains {
    param(
        [string]$Text,
        [string]$Token,
        [string]$Message
    )

    if ($Text.IndexOf($Token, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw "[$Message] missing: $Token"
    }
}

function Assert-NotContains {
    param(
        [string]$Text,
        [string]$Token,
        [string]$Message
    )

    if ($Text.IndexOf($Token, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        throw "[$Message] forbidden: $Token"
    }
}

$skillPath = Join-Path $SkillRoot 'SKILL.md'
$skillText = Get-Content -LiteralPath $skillPath -Raw -Encoding UTF8
$scenarioPath = Join-Path $SkillRoot 'tests\fixtures\skill-scenarios.md'
$scenarioText = Get-Content -LiteralPath $scenarioPath -Raw -Encoding UTF8

Assert-Contains $skillText 'nuomi-drama-scripts' 'skill-name'
Assert-Contains $skillText 'brief/bible/beats gate' 'batch-gate'
Assert-Contains $skillText 'abstract style features' 'style-safety'
Assert-Contains $skillText 'needs_review' 'docx-not-ready'
Assert-Contains $skillText 'rights_blocked' 'rights-block'
Assert-Contains $skillText 'screenplay.md' 'handoff-file'
Assert-Contains $skillText 'does not depend on `nuomi-drama-skills`' 'runtime-isolation'
Assert-Contains $skillText 'impact analysis' 'fact-change'

foreach ($token in @('batch-gate', 'style-safety', 'docx-gate', 'fact-change', 'rights-state', 'runtime-isolation', 'dramaclaw-handoff')) {
    Assert-Contains $scenarioText $token 'scenario-fixture'
}

$handoffPath = Join-Path $SkillRoot 'templates\screenplay.md'
if (Test-Path -LiteralPath $handoffPath) {
    $handoffText = Get-Content -LiteralPath $handoffPath -Raw -Encoding UTF8
    foreach ($forbidden in @('image_prompt', 'video_prompt', 'provider', 'model_parameters')) {
        Assert-NotContains $handoffText $forbidden 'handoff-media-isolation'
    }
}
else {
    throw "[handoff-file] missing template: $handoffPath"
}

Write-Output 'PASS: nuomi-drama-scripts skill contract'
