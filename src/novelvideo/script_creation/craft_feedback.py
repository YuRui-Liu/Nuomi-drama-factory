"""Reconstruct authored history and verified dispositions from durable stages."""
import re


def qc_memory(memory):
    """Keep historical evidence, without author claims or live-looking issue IDs."""
    def historical(value):
        if isinstance(value, dict):
            return {key: True if key == 'qc_resolution' else historical(item)
                    for key, item in value.items()
                    if key not in {'id', 'prior_issue_id', 'resolved_issue_ids', 'history_issue_ids'}}
        if isinstance(value, list):
            return [historical(item) for item in value
                    if not isinstance(item, dict) or 'author_attempt' not in item]
        if isinstance(value, str):
            # Reviewer prose can also mention old IDs; only issue_history may
            # supply identifiers usable in this task's resolution/lineage fields.
            return re.sub(r'(?<![A-Za-z0-9_])issue_[0-9a-f]{20}(?![A-Za-z0-9_])', '[历史问题]', value)
        return value

    return historical(list(memory))


def author_history(records):
    return [{'stage': r.stage, 'output': r.output} for r in records if 'markdown' in r.output]


def repair_history(records):
    events = []
    for record in records:
        output = record.output
        if output.get('repair_notes'):
            events.append({'stage': record.stage, 'draft_hash': record.output_hash,
                           'attempted_repairs': output['repair_notes'], 'authority': 'author_claim_only'})
        if 'issues' in output:
            events.append({'stage': record.stage, 'summary': output['summary'],
                           'issues': output['issues'], 'resolved_issue_ids': output.get('resolved_issue_ids', []),
                           'authority': 'qc_assessment_not_project_fact'})
    return events
