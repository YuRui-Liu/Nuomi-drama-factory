"""Retain media as history while marking changed static storyboard descendants."""
from novelvideo.freezone.orchestration.models import input_fingerprint


def invalidate_static_outputs(existing, payload):
    if not existing:
        return
    old = {n['id']: n for n in existing.get('nodes', [])}
    new = {n['id']: n for n in payload.get('nodes', [])}

    def signature(canvas, board, nodes):
        data = board.get('data', {})
        bound = {b.get('node_id') for b in data.get('referenceBindings', [])}
        edges = sorted((e.get('source'), e.get('target')) for e in canvas.get('edges', [])
                       if e.get('target') == board['id'] and e.get('source') in bound)
        media = [(key, (nodes.get(key) or {}).get('data', {}).get('imageUrl'),
                  (nodes.get(key) or {}).get('data', {}).get('assetVersionId')) for key in sorted(bound)]
        return input_fingerprint(board), edges, media

    affected = set()
    for key, node in new.items():
        previous = old.get(key)
        if not previous or node.get('type') != 'storyboardGenNode':
            continue
        if not any(f.get('static_panel') for f in node.get('data', {}).get('frames', [])):
            continue
        if signature(existing, previous, old) != signature(payload, node, new):
            affected.add(key)
        elif previous.get('data', {}).get('imageUrl') != node.get('data', {}).get('imageUrl'):
            affected.update(e.get('target') for e in payload.get('edges', [])
                            if e.get('source') == key and not (e.get('data') or {}).get('contextLink'))
    pending = list(affected)
    while pending:
        source = pending.pop()
        for edge in payload.get('edges', []):
            target = edge.get('target')
            if edge.get('source') == source and target not in affected and not (edge.get('data') or {}).get('contextLink'):
                affected.add(target)
                pending.append(target)
    for key in affected:
        if key in new:
            data = new[key].setdefault('data', {})
            data['orchestration'] = {**(data.get('orchestration') or {}), 'status': 'stale',
                                     'message': '分镜输入或参考已变化，保留旧产物供比较，请重新生成。'}
