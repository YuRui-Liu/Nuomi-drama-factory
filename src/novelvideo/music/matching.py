"""Explainable text/tag matching, with no generation side effects."""


def match_assets(assets, query='', duration_ms=0, instrumental_only=False):
    words = query.lower().split()
    result = []
    for asset in assets:
        if instrumental_only and asset.get('vocals') != 'none':
            continue
        haystack = ' '.join([asset['name'], asset.get('description', ''), *asset.get('tags', [])]).lower()
        matched = [word for word in words if word in haystack]
        if words and not matched:
            continue
        enough = asset['durationMs'] >= duration_ms
        result.append({**asset, 'matchReasons': matched or ['库内音乐'],
                       'shortcomings': [] if enough else ['长度不足，需裁短或设置循环'],
                       '_score': len(matched) * 3 + int(enough)})
    return [{k:v for k,v in a.items() if k != '_score'} for a in sorted(result, key=lambda a: a['_score'], reverse=True)]
