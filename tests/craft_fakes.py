"""Deterministic provider replies; real craft validation still runs in tests."""
import json


def craft_reply(prompt: str, markdown: str) -> dict:
    try:
        request, _ = json.JSONDecoder().raw_decode(prompt)
    except ValueError:
        return {'markdown': markdown}
    if 'review' not in request.get('stage', ''):
        return {'markdown': markdown, 'repair_notes': [dict(issue_id=i['id'],diagnosis=i['reason'],
            strategy='根据证据修改',changed_quote=markdown) for i in (request.get('review') or {}).get('issues',[])
            if i['severity'] == 'blocker']}
    draft = request['draft']
    return {'summary': '检查完成，保留人物行动及其后果。',
            'coverage': ['因果、人物、场景、对白、信息边界'],
            'strengths': [{'quote': draft, 'start': 0, 'end': len(draft), 'reason': '保留行动与后果'}],
            'issues': [],
            'resolved_issue_ids': [i['id'] for i in request.get('issue_history', []) if i['severity'] == 'blocker']}
