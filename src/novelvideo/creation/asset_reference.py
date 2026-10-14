"""Read-only asset selection and verified, immutable presentation references."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import re
import math

from novelvideo.models import NovelCharacter
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.production_workflow.coordination import asset_project_lock
from novelvideo.utils.static_urls import project_static_url
from .asset_media import snapshot, controlled_path, _digest, owns_slot, SCENE_MANIFEST_PATHS
from .asset_sync import fingerprint, SourceSyncPending
from .models import AssetRef
from .store import ObjectStore


class AssetReferenceError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@contextmanager
def readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('BEGIN')
        yield conn
    finally:
        conn.close()


def table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def fail(code, message):
    raise AssetReferenceError(code, message)


def verify_blob(root, blob):
    root = Path(root)
    try:
        if (not isinstance(blob, dict) or not isinstance(blob.get('path'), str)
                or Path(blob['path']).is_absolute() or type(blob.get('size')) is not int or blob['size'] < 0
                or not isinstance(blob.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', blob['sha256'])):
            raise ValueError('Invalid archive proof')
        path = controlled_path(root, blob['path'])
        prefix = root.absolute() / 'assets' / '.creation-media' / 'sha256'
        if (path.parent != prefix / blob['sha256'][:2] or path.stem != blob['sha256']
                or _digest(path) != (blob['sha256'], blob['size'])):
            raise ValueError('Invalid archive')
    except (OSError, ValueError, KeyError, TypeError):
        fail('asset_media_unavailable', '固定素材档案缺失或校验失败')


MEDIA_FIELDS = ('imageUrl', 'previewImageUrl', 'panoUrl', 'plyUrl', 'sources', 'assetSceneManifest',
                'activeSourceId', 'scenesBySourceId', 'layersBySourceId', 'scene')


def protect_bindings(existing, payload):
    """Autosave may edit presentation, never manufacture fixed asset evidence."""
    before = {node['id']: node for node in (existing or {}).get('nodes', [])}
    for node in payload.get('nodes', []):
        old = before.get(node['id'], {})
        previous, current = old.get('data', {}), node.get('data', {})
        if not previous.get('assetRef') and not current.get('assetRef') and not current.get('assetMediaSelection'):
            continue
        def same(condition):
            if not condition:
                from novelvideo.freezone.canvas_store import CanvasAssetBindingReadonly
                raise CanvasAssetBindingReadonly('asset_binding_readonly: 固定资产引用须通过资产操作更新')
        same(old.get('type') == node.get('type'))
        for key in ('assetRef', 'assetMediaSelection', 'assetSceneManifest'):
            same(previous.get(key) == current.get(key))
        if node.get('type') != 'threeDWorldNode':
            for key in MEDIA_FIELDS:
                same(previous.get(key) == current.get(key))
            continue
        sources = previous.get('sources') or []
        updated = current.get('sources') or []
        same(isinstance(sources, list) and isinstance(updated, list))
        same(all(isinstance(item, dict) for item in [*sources, *updated]))
        for item in updated:
            transform = item.get('transform')
            if transform is not None:
                same(isinstance(transform, dict) and set(transform) <= {'xOffset', 'yOffset', 'zOffset', 'yawDeg', 'pitchDeg', 'rollDeg', 'scale'})
                same(all(type(value) in {int, float} and math.isfinite(value) for value in transform.values()))
        # Only transform is a local view change. All unknown/new source fields
        # remain protected, including every URL and filesystem locator.
        stable = lambda values: [{k: v for k, v in item.items() if k not in {'transform', 'current'}} for item in values]
        same(stable(sources) == stable(updated))
        selected = next((item for item in sources if item.get('id') == current.get('activeSourceId')), None)
        same(selected is not None)
        same(current.get('plyUrl') == selected.get('ply_url'))
        same(current.get('panoUrl') == selected.get('pano_url', previous.get('panoUrl')))
        for key in ('imageUrl', 'previewImageUrl'):
            same(previous.get(key) == current.get(key))


class AssetReferences:
    def __init__(self, state_dir, output_dir, project_id):
        self.state_dir, self.output_dir = Path(state_dir), Path(output_dir)
        self.project_id = project_id
        self.db_path = self.state_dir / 'creation' / 'objects.sqlite3'

    def _records(self):
        path = self.state_dir / 'data.db'
        if not path.is_file():
            return []
        records = []
        with readonly(path) as conn:
            if table(conn, 'characters'):
                for row in conn.execute('SELECT * FROM characters ORDER BY name'):
                    value = dict(row)
                    value['aliases'] = json.loads(value.pop('aliases_json', '[]') or '[]')
                    record = NovelCharacter.model_validate({k: v for k, v in value.items() if k in NovelCharacter.model_fields and v is not None})
                    records.append(('character', record.name, '', record))
                    for identity in record.identities:
                        records.append(('character', identity.identity_id, record.name, identity))
            if table(conn, 'scenes'):
                for row in conn.execute('SELECT * FROM scenes ORDER BY name'):
                    record = SQLiteStore._row_to_scene(row)
                    records.append(('scene', record.name, record.base_scene_id or '', record))
        return records

    def catalog(self, *, selection=None):
        mappings = {}
        if self.db_path.is_file():
            with readonly(self.db_path) as conn:
                if table(conn, 'asset_sources'):
                    for row in conn.execute('SELECT s.*, o.current_revision FROM asset_sources s JOIN objects o ON o.id=s.object_id'):
                        mappings[row['object_id']] = dict(row)
        source = SimpleNamespace(project_dir=str(self.output_dir), state_dir=self.state_dir)
        entries = []
        records = self._records()
        for kind, name, parent, record in records:
            locator = {'kind': kind, 'source_name': name, 'parent_source_name': parent}
            if selection is not None and locator != selection:
                continue
            mapping = {'kind': kind, 'source_name': name, 'parent_id': parent}
            # snapshot only uses parent_id as the base/variant discriminator.
            value = snapshot(source, mapping, record, archive=False)
            registered = next((m for m in mappings.values() if m['kind'] == kind and m['source_name'] == name
                               and (mappings.get(m['parent_id'], {}).get('source_name', '') == parent)), None)
            ref = None if registered is None else {'base_asset_id': registered['parent_id'] or registered['object_id'],
                'variant_id': registered['object_id'] if registered['parent_id'] else None, 'revision': registered['current_revision']}
            parent_record = next((r.model_dump(mode='json') for k, n, p, r in records if k == kind and n == parent and not p), None)
            entries.append({**locator, 'selection': locator, 'proof': fingerprint({'selection': locator, 'snapshot': value, 'parent': parent_record}),
                            'title': getattr(record, 'identity_name', None) or record.name,
                            'asset_ref': ref, 'snapshot': value, 'media': self._previews(mapping, value)})
        return entries

    def _previews(self, mapping, payload):
        from .asset_media import scene_media_layouts
        from novelvideo.utils.path_resolver import canonical_identity_portrait_path, canonical_identity_path, canonical_identity_costume_path, compute_identity_portrait_path
        layouts = {}
        if mapping['kind'] == 'scene':
            layouts = scene_media_layouts(self.output_dir, payload['name'])[0]
        elif mapping['parent_id']:
            for role, resolver in [('portrait', canonical_identity_portrait_path), ('state', canonical_identity_path), ('costume', canonical_identity_costume_path)]:
                layouts[role] = resolver(self.output_dir, payload['character_name'], payload['identity_name'])
            layouts['portrait'] = compute_identity_portrait_path(self.output_dir, payload['character_name'], payload['identity_name']) or layouts['portrait']
        else:
            layouts['portrait'] = self.output_dir / 'assets' / 'characters' / payload['name'] / 'portrait.png'
        results = []
        for item in payload.get('_media', []):
            role = self.role(item)
            blob = item.get('current_blob') or item.get('canonical_blob') or item['blob']
            preview = None
            for value in (layouts.get(role), item.get('provenance', {}).get('canonical_path'), item.get('source_path')):
                if not value:
                    continue
                path = controlled_path(self.output_dir, value)
                if path.is_file() and _digest(path) == (blob['sha256'], blob['size']):
                    preview = project_static_url(self.project_id, path.relative_to(self.output_dir.absolute()).as_posix())
                    break
            results.append({'slot_id': item['slot_id'], 'role': role, 'preview_url': preview,
                            'sha256': blob['sha256'], 'adoption_status': item['adoption_status']})
        return results

    async def register(self, selection, proof, *, actor):
        from .asset_adapter import AssetAdapter, registration_io
        async with asset_project_lock(self.state_dir):
            if not isinstance(selection, dict) or set(selection) != {'kind', 'source_name', 'parent_source_name'} or selection['kind'] not in {'character', 'scene'}:
                fail('asset_selection_invalid', '资产选择字段无效')
            selection = dict(selection)
            records = self._records()
            kind, parent = selection['kind'], selection['parent_source_name']
            locator = parent or selection['source_name']
            matches = [(n, p, r) for k, n, p, r in records if k == kind and (kind == 'scene' or not p)
                       and locator in {n, *getattr(r, 'aliases', [])}]
            if len(matches) != 1:
                fail('asset_selection_ambiguous', '资产名称不存在或别名不唯一')
            # Check the supplied locator before normalization, so a retired
            # canonical name cannot return through an alias of its successor.
            from .asset_rename import assert_asset_locators_writable, assert_no_pending_rename
            guard_source = SimpleNamespace(db_path=self.state_dir / 'data.db')
            try:
                assert_no_pending_rename(guard_source, kind=kind, names={locator})
                assert_asset_locators_writable(guard_source, kind, {locator})
            except SourceSyncPending:
                fail('asset_media_pending', '资产名称等待对账')
            except (ValueError, RuntimeError):
                fail('asset_source_conflict', '资产名称已退休或等待对账')
            if parent:
                selection['parent_source_name'] = matches[0][0]
                children = [(n, r) for k, n, p, r in records if k == kind and p == selection['parent_source_name']]
                exact = [(n, r) for n, r in children if n == selection['source_name']]
                if kind == 'character' and exact:
                    candidates = exact
                else:
                    candidates = [(n, r) for n, r in children if selection['source_name'] in
                                  {n, getattr(r, 'identity_name', ''), *getattr(r, 'aliases', [])}]
                if len(candidates) != 1:
                    fail('asset_selection_ambiguous', '资产变体名称不存在或不唯一')
                selection['source_name'] = candidates[0][0]
            else:
                selection['source_name'] = matches[0][0]
            matches = await registration_io(self.catalog, selection=selection)
            if len(matches) != 1 or matches[0]['proof'] != proof:
                fail('asset_selection_stale', '资产内容已变化，请重新选择')
            source = SQLiteStore('asset-reference', output_dir=str(self.output_dir), state_dir=str(self.state_dir))
            try:
                adapter = AssetAdapter(source, ObjectStore(self.state_dir), actor)
                parent = selection['parent_source_name']
                base = await adapter.register_base(selection['kind'], parent or selection['source_name'])
                return await adapter.register_variant(base.base_asset_id, selection['source_name']) if parent else base
            except SourceSyncPending:
                fail('asset_media_pending', '资产源等待对账，固定历史版本仍可读取')
            except RuntimeError:
                fail('asset_source_conflict', '资产源存在未确认变化，请显式刷新或对账')
            finally:
                await source.close()

    @staticmethod
    def fixed(conn, ref):
        if not table(conn, 'asset_sources'):
            fail('asset_reference_unknown', '资产尚未注册')
        base = conn.execute('SELECT * FROM asset_sources WHERE object_id=?', (ref.base_asset_id,)).fetchone()
        target = conn.execute('SELECT * FROM asset_sources WHERE object_id=?', (ref.variant_id or ref.base_asset_id,)).fetchone()
        if base is None or target is None or base['parent_id'] or base['kind'] not in {'character', 'scene'} or target['kind'] != base['kind']:
            fail('asset_reference_unknown', '资产引用不属于此项目')
        if ref.variant_id and target['parent_id'] != ref.base_asset_id:
            fail('asset_reference_unknown', '变体不属于此基础资产')
        obj = ObjectStore._read(conn, target['object_id'], ref.revision)
        if obj is None or obj['kind'] != target['kind'] or obj['base_asset_id'] != ref.base_asset_id or obj['variant_id'] != ref.variant_id:
            fail('asset_reference_unknown', '资产版本不存在或归属不符')
        return obj, dict(target)

    def resolve(self, ref):
        ref = AssetRef.model_validate(ref)
        if not self.db_path.is_file():
            fail('asset_reference_unknown', '资产尚未注册')
        with readonly(self.db_path) as conn:
            obj, mapping = self.fixed(conn, ref)
        return {'asset_ref': ref.model_dump(), 'object': obj, 'mapping': mapping}

    def uses(self, base_id):
        from novelvideo.freezone.canvas_store import read_canvas
        if not self.db_path.is_file():
            return []
        results = []
        with readonly(self.db_path) as conn:
            if not table(conn, 'relations'):
                return []
            rows = conn.execute("SELECT payload FROM relations WHERE kind='asset_binding'").fetchall()
            for row in rows:
                value = json.loads(row['payload'])
                if value['asset_ref']['base_asset_id'] != base_id:
                    continue
                operation = conn.execute('SELECT state FROM operations WHERE id=?', (value['operation_id'],)).fetchone()
                status = operation['state'] if operation else 'missing'
                if status == 'applied':
                    canvas = read_canvas(self.state_dir, value['canvas_id'])
                    node = next((n for n in (canvas or {}).get('nodes', []) if n['id'] == value['node_id']), None)
                    if (node is None or (canvas or {}).get('project_id') != self.project_id
                            or node.get('data', {}).get('assetRef') != value['asset_ref']):
                        status = 'missing'
                results.append({**value, 'status': status})
        return results

    def materialize(self, ref, media, node_type):
        resolved = self.resolve(ref)
        if node_type not in {'uploadNode', 'threeDWorldNode'}:
            fail('asset_media_type', '此节点不能承载固定资产，请使用素材节点')
        if not isinstance(media, dict) or set(media) != {'slot_id', 'role'}:
            fail('asset_media_selection', '必须指定素材槽位和角色')
        payload = resolved['object']['payload']
        items = payload.get('_media', [])
        selected = [item for item in items if item['slot_id'] == media['slot_id'] and self.role(item) == media['role']]
        if len(selected) != 1:
            fail('asset_media_selection', '素材不属于此固定资产版本')
        if not owns_slot(resolved['mapping'], payload, media['slot_id']):
            fail('asset_media_selection', '素材槽位归属不符')
        blobs = []
        verified = {}
        def url(item):
            blob = item.get('current_blob') or item.get('canonical_blob') or item.get('blob')
            if not isinstance(blob, dict):
                fail('asset_media_unavailable', '此版本没有可用素材')
            key = (blob.get('path'), blob.get('sha256'), blob.get('size'))
            if key not in verified:
                verify_blob(self.output_dir, blob)
                blobs.append(blob)
                verified[key] = project_static_url(self.project_id, blob['path'])
            return verified[key]
        data = {field: None for field in MEDIA_FIELDS}
        chosen = selected[0]
        if node_type == 'uploadNode':
            image_roles = {'portrait', 'state', 'costume', 'master', 'reverse_master', 'pano', 'panorama_360', 'spatial_layout'}
            if media['role'] not in image_roles:
                fail('asset_media_type', '此素材不是图片')
            blob = chosen.get('current_blob') or chosen.get('canonical_blob') or chosen.get('blob') or {}
            if Path(blob.get('path', '')).suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.avif', '.bmp', '.ico'}:
                fail('asset_media_type', '此固定素材不是受支持的图片格式')
            data.update(imageUrl=url(chosen), previewImageUrl=url(chosen))
        else:
            if resolved['object']['kind'] != 'scene' or media['role'] not in {'ply', 'custom_scene', 'pano_ply', 'pano_depth_ply', 'master_ply', 'reverse_ply', 'pano', 'panorama_360'}:
                fail('asset_media_type', '此素材不是场景模型')
            manifest = payload.get('_scene_manifest', {})
            if manifest.get('bundle_sources') and media['role'] == manifest.get('bundle_primary_role'):
                sources = []
                for source in manifest['bundle_sources']:
                    sources.append({key: deepcopy(value) for key,value in source.items() if key != 'media'} | {
                        field:url({'current_blob':blob}) for field,blob in source['media'].items()})
                active = next((source for source in sources if source['id'] == manifest.get('active_source_id')), None)
                if active is None:
                    fail('asset_media_unavailable', '固定场景的当前来源不存在')
                scenes = deepcopy(manifest.get('scenes_by_source_id') or {})
                fixed_manifest = deepcopy(manifest)
                fixed_manifest.update(sources=deepcopy(sources), active_source=deepcopy(active))
                # Never expose mutable canonical paths inside the fixed bundle.
                fixed_manifest.pop('bundle_sources', None)
                for field in SCENE_MANIFEST_PATHS:
                    fixed_manifest.pop(field, None)
                data.update(plyUrl=active.get('ply_url'), panoUrl=active.get('pano_url'),
                            previewImageUrl=active.get('pano_url'), sources=sources,
                            activeSourceId=active['id'], scenesBySourceId=scenes, layersBySourceId={},
                            scene=deepcopy(scenes.get(active['id'])), assetSceneManifest=fixed_manifest,
                            assetRef=resolved['asset_ref'], assetMediaSelection=media)
                return {'data':data, 'blobs':blobs, 'proof':fingerprint(resolved), 'resolved':resolved}
            fixed_manifest = {k: v for k, v in manifest.items() if k not in SCENE_MANIFEST_PATHS}
            for item in items:
                field = item.get('manifest_field')
                if field in SCENE_MANIFEST_PATHS:
                    fixed_manifest[field] = url(item)
            for field, value in manifest.items():
                if field in SCENE_MANIFEST_PATHS and value and field not in fixed_manifest:
                    fail('asset_media_unavailable', '固定场景依赖素材缺失')
            collision = fixed_manifest.get('collision_glb_path')
            pano = next((item for item in items if self.role(item) in {'pano', 'panorama_360'}), None)
            pano_url = url(pano) if pano else None
            sources = []
            for item in items:
                role = self.role(item)
                if role in {'ply', 'custom_scene', 'pano_ply', 'pano_depth_ply', 'master_ply', 'reverse_ply'}:
                    blob = item.get('current_blob') or item.get('canonical_blob') or item.get('blob') or {}
                    if Path(blob.get('path', '')).suffix.lower() not in {'.sog', '.ply', '.splat', '.ksplat'}:
                        fail('asset_media_type', '此固定素材不是受支持的场景模型格式')
                    sources.append({'id': role, 'source_type': 'sog',
                                    'source_kind': {'custom_scene': 'custom', 'ply': 'active', 'pano_ply': 'pano',
                                                    'pano_depth_ply': 'pano', 'master_ply': 'master', 'reverse_ply': 'reverse'}[role],
                                    'ply_url': url(item), 'pano_url': pano_url,
                                    'collision_glb_url': collision, 'current': role == media['role']})
            for item in items:
                role = self.role(item)
                if role in {'pano', 'panorama_360'}:
                    sources.append({'id': role, 'source_type': 'pano360', 'source_kind': 'pano',
                                    'pano_url': url(item), 'current': role == media['role']})
            preview = next((item for item in items if self.role(item) == 'master'), None)
            panorama = media['role'] in {'pano', 'panorama_360'}
            scenes, selected_scene = self._scene_layouts(payload, items, sources, media['role'])
            if 'sources' in fixed_manifest:
                fixed_manifest['sources'] = deepcopy(sources)
            fixed_manifest.update(scenes_by_source_id=scenes, active_source_id=media['role'], scene=selected_scene,
                                  active_source=deepcopy(next(source for source in sources if source['id'] == media['role'])))
            data.update(plyUrl=None if panorama else url(chosen), panoUrl=url(chosen) if panorama else pano_url,
                        previewImageUrl=url(preview) if preview else None, sources=sources,
                        activeSourceId=media['role'], scenesBySourceId=scenes, layersBySourceId={},
                        scene=selected_scene, assetSceneManifest=fixed_manifest)
        data.update(assetRef=resolved['asset_ref'], assetMediaSelection=media)
        return {'data': data, 'blobs': blobs, 'proof': fingerprint(resolved), 'resolved': resolved}

    def _scene_layouts(self, payload, items, sources, selected_role):
        """Translate saved source identities using only the immutable snapshot.

        Source URLs are identity evidence, never read from the current scene
        directory. Keep unrecognized saved layouts rather than discarding them.
        """
        from novelvideo.director_world.stage_manifest import stage_dir
        manifest = payload.get('_scene_manifest') or {}
        normalize = lambda value: str(value or '').split('#', 1)[0].split('?', 1)[0]
        identities = {}
        for source in sources:
            role = source['id']
            item = next(item for item in items if self.role(item) == role)
            paths = [item.get('source_path'), item.get('provenance', {}).get('canonical_path')]
            field = item.get('manifest_field')
            if field and manifest.get(field):
                original = stage_dir(self.output_dir, payload['name']) / manifest[field]
                paths.append(original)
                blob = item.get('current_blob') or item.get('canonical_blob') or item.get('blob') or {}
                if original.suffix == '.ply' and Path(blob.get('path', '')).suffix == '.sog':
                    paths.append(original.with_suffix('.sog'))
            urls = {normalize(source.get('ply_url') or source.get('pano_url'))}
            for value in paths:
                if not value:
                    continue
                path = Path(value)
                if path.is_absolute():
                    try:
                        path = path.relative_to(self.output_dir.absolute())
                    except ValueError:
                        continue
                if '..' not in path.parts:
                    urls.add(normalize(project_static_url(self.project_id, path.as_posix())))
            identities[role] = (source['source_kind'], source['source_type'], urls)

        def mapped(old_id):
            if old_id in identities:
                return old_id
            parts = str(old_id or '').split(':', 3)
            active = manifest.get('active_source') or {}
            if not isinstance(active, dict):
                active = {}
            active_id = active.get('id') or manifest.get('active_source_id')
            active_url = active.get('ply_url') or active.get('url') or active.get('pano_url')
            if not active_url and (active.get('fs') or active.get('pano_fs')):
                path = Path(active.get('fs') or active['pano_fs'])
                if path.is_absolute():
                    try:
                        path = path.relative_to(self.output_dir.absolute())
                    except ValueError:
                        path = None
                if path is not None and '..' not in path.parts:
                    active_url = project_static_url(self.project_id, path.as_posix())
            if old_id == active_id and active_url:
                kind, source_type = active.get('source_kind'), active.get('source_type')
                old_url = normalize(active_url)
            elif len(parts) == 4 and parts[0] in {'legacy', 'source', 'manifest-source'}:
                kind, source_type, old_url = parts[1], parts[2], normalize(parts[3])
            elif str(old_id).startswith('node:'):
                node_parts = str(old_id).split(':', 2)
                if len(node_parts) != 3:
                    return old_id
                kind, source_type, old_url = None, node_parts[1], normalize(node_parts[2])
            elif old_id == f"scene-pano:{payload['name']}":
                # Without a saved source URL only a unique fixed panorama is
                # sufficient evidence. Never guess between two image roles.
                candidates = [role for role, (_, typ, _) in identities.items() if typ == 'pano360']
                if len(candidates) == 1:
                    return candidates[0]
                fail('asset_scene_layout_conflict', '保存的全景布局缺少唯一来源证据')
            else:
                return old_id
            matches = [role for role, (candidate_kind, candidate_type, urls) in identities.items()
                       if (kind is None or candidate_kind == kind or candidate_kind == 'custom' and kind == 'uploaded')
                       and candidate_type == source_type and old_url in urls]
            if len(matches) > 1:
                fail('asset_scene_layout_conflict', '固定场景来源与保存布局存在歧义')
            return matches[0] if matches else old_id

        def converted(snapshot, source_id):
            result = deepcopy(snapshot)
            if isinstance(result, dict):
                result['world'] = {**(result.get('world') or {}), 'activeSourceId': source_id}
            return result

        scenes = {}
        for old_id, snapshot in (manifest.get('scenes_by_source_id') or {}).items():
            new_id = mapped(old_id)
            value = converted(snapshot, new_id)
            if new_id in scenes and scenes[new_id] != value:
                fail('asset_scene_layout_conflict', '固定场景包含冲突的保存布局')
            scenes[new_id] = value
        active_scene = manifest.get('scene')
        active_id = manifest.get('active_source_id') or ((active_scene or {}).get('world') or {}).get('activeSourceId')
        if active_scene is not None and active_id:
            # The top-level scene is the current saved view for its own source,
            # not a default view for every source selected later.
            active_id = mapped(active_id)
            scenes[active_id] = converted(active_scene, active_id)
        return scenes, deepcopy(scenes.get(selected_role))

    @staticmethod
    def role(item):
        if item.get('media_role'):
            return item['media_role']
        slot = item['slot_id']
        suffix = slot.rsplit(':', 1)[-1]
        if slot.startswith('scene:') or suffix in {'portrait', 'costume'}:
            return suffix
        return 'state' if ':state:' in slot else suffix
