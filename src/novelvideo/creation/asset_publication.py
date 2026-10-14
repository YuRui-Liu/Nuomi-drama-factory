"""Explicit canvas-to-library saves with durable request and media receipts.

The source SQL, files, and semantic DB are not one transaction. Each crossing
has durable ownership/evidence; unknown partial media writes stay fail-closed.
"""
from contextlib import AsyncExitStack
from copy import deepcopy
import json
import hashlib
import math
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

from pydantic import TypeAdapter

from novelvideo.freezone.canvas_store import read_canvas
from novelvideo.production_workflow.coordination import asset_project_lock
from novelvideo.sqlite_store import SQLiteStore
from novelvideo.utils.safe_paths import validate_path_segment
from .asset_adapter import AssetAdapter, registration_io
from .asset_media import capture_blob, controlled_path, _digest
from .asset_media_publication import MediaPublication, media_publication
from .asset_publication_models import AssetPublicationRequest
from .asset_reference import AssetReferences, fail, readonly, table
from .asset_sync import SourceSync, SourceSyncPending, fingerprint
from .canvas_workspaces import list_canvases
from .models import AssetRef
from .store import ObjectStore


def _checkpoint(phase):
    """Fault seam at durable boundaries."""


BASE_FIELDS = {
    'character': {'description', 'face_prompt', 'appearance_details', 'age_group', 'body_type', 'gender', 'role'},
    'scene': {'description', 'environment_prompt', 'time_of_day', 'scene_type', 'notes'},
}
SOURCE_URL_FIELDS = ('imageUrl', 'previewImageUrl', 'panoUrl', 'plyUrl')
SOURCE_FIELDS = ('url', 'ply_url', 'pano_url', 'collision_glb_url', 'voxel_json_url')


class SharedAssetPublication:
    def __init__(self, ctx):
        self.ctx = ctx
        self.state_dir, self.root = Path(ctx.state_dir), Path(ctx.output_dir)
        self.db_path = self.state_dir/'creation/objects.sqlite3'
        self.references = AssetReferences(self.state_dir, self.root, ctx.project_id)

    def _url_path(self, url):
        parsed = urlsplit(url)
        prefixes = (f'/api/v1/projects/{self.ctx.project_id}/media/', f'/static/projects/{self.ctx.project_id}/')
        prefix = next((p for p in prefixes if parsed.path.startswith(p)), None)
        if parsed.scheme or parsed.netloc or not prefix:
            fail('scope_violation', '素材 URL 不属于当前项目')
        return controlled_path(self.root, unquote(parsed.path[len(prefix):]))

    def inspect_source(self, canvas_id, node_id):
        """Read-only selection proof. Never initialize source or semantic stores."""
        if not any(c['canvas_id'] == canvas_id and c['status'] == 'applied' for c in list_canvases(self.state_dir)):
            fail('scope_violation', '请选择项目中已注册的画布')
        canvas = read_canvas(self.state_dir, canvas_id)
        if not canvas or canvas.get('project_id') != self.ctx.project_id:
            fail('scope_violation', '画布不属于当前项目')
        nodes = [n for n in canvas.get('nodes', []) if n.get('id') == node_id]
        if len(nodes) != 1:
            fail('asset_source_missing', '所选画布节点不存在')
        node = nodes[0]
        if node.get('type') not in {'uploadNode','imageGenNode','imageNode','exportImageNode',
                                    'storyboardGenNode','pano360ViewerNode','threeDWorldNode'}:
            fail('asset_source_invalid', '此节点类型不能发布为角色或场景素材')
        if not isinstance(node.get('data'), dict):
            fail('asset_source_invalid', '节点媒体数据格式无效')
        data = deepcopy(node.get('data') or {})
        files = {}
        values = [data.get(k) for k in SOURCE_URL_FIELDS]
        sources = data.get('sources')
        if sources is not None and not isinstance(sources, list):
            fail('asset_source_invalid', '场景来源必须是列表')
        for source in sources or []:
            if not isinstance(source, dict):
                fail('asset_source_invalid', '场景来源格式无效')
            values.extend(source.get(k) for k in SOURCE_FIELDS)
        for value in values:
            if value is not None and not isinstance(value, str):
                fail('asset_source_invalid', '媒体 URL 必须是字符串')
            if value and value not in files:
                path = self._url_path(value)
                try:
                    files[value] = capture_blob(self.root, path, archive=False)
                except ValueError:
                    fail('asset_source_missing', '画布素材文件不可用或正在变化')
        if not files:
            fail('asset_source_missing', '节点没有可发布的媒体')
        evidence = {'canvas_id':canvas_id, 'node_id':node_id, 'type':node.get('type'), 'data':data, 'files':files}
        return {**evidence, 'proof':hashlib.sha256(fingerprint(evidence).encode()).hexdigest()}

    def _init(self):
        objects = ObjectStore(self.state_dir)
        with objects.transaction() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS shared_asset_publications (
                request_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                request TEXT NOT NULL, phase TEXT NOT NULL, plan TEXT NOT NULL, result TEXT)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS asset_media_receipts (
                request_key TEXT PRIMARY KEY, result TEXT)''')
        return objects

    def _row(self, request_id):
        if not self.db_path.is_file():
            return None
        with readonly(self.db_path) as conn:
            if not table(conn, 'shared_asset_publications'):
                return None
            row = conn.execute('SELECT * FROM shared_asset_publications WHERE request_id=?', (request_id,)).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _key(request_id):
        return 'shared-publication:' + request_id

    def _receipt(self, request_id):
        with readonly(self.db_path) as conn:
            if not table(conn, 'asset_media_receipts'):
                return None
            row = conn.execute('SELECT result FROM asset_media_receipts WHERE request_key=?', (self._key(request_id),)).fetchone()
            return json.loads(row['result']) if row and row['result'] else None

    def _result(self, row):
        if row['result']:
            return json.loads(row['result'])
        revisions = self._receipt(row['request_id'])
        if not revisions:
            return None
        plan = json.loads(row['plan'])
        ref = plan['registered_ref']
        object_id = ref['variant_id'] or ref['base_asset_id']
        if object_id not in revisions:
            raise SourceSyncPending('Publication receipt does not include its target')
        ref = {**ref, 'revision':revisions[object_id]}
        fixed = self.references.resolve(ref)['object']['payload']
        request = json.loads(row['request'])
        roles = {'identity':'state','identity_costume':'costume','identity_portrait':'portrait',
                 'scene_master':'master','scene_reverse_master':'reverse_master','scene_spatial_layout':'spatial_layout',
                 'scene_360':'panorama_360','scene_director_pano_360':'pano','scene_3gs_custom_scene':'custom_scene',
                 'scene_3gs_master_ply':'master_ply','scene_3gs_reverse_ply':'reverse_ply',
                 'scene_3gs_pano_ply':'pano_ply','scene_3gs_active_ply':'ply'}
        role = fixed.get('_scene_manifest', {}).get('bundle_primary_role') if request.get('scene_bundle') else roles.get(request['role'],request['role'])
        media = [{'slot_id':m['slot_id'], 'role':self.references.role(m)} for m in fixed.get('_media', [])]
        chosen = [m for m in media if m['role'] == role]
        if len(chosen) != 1:
            raise SourceSyncPending('Committed publication does not have one exact target media selection')
        return {'request_id':row['request_id'], 'status':'applied', 'asset_ref':ref,
                'media':media, 'media_selection':chosen[0]}

    def status(self, request_id):
        row = self._row(request_id)
        if row is None:
            fail('asset_publication_missing', '保存请求不存在')
        return self._result(row) or {'request_id':request_id, 'status':'reconciliation_required' if row['phase'] == 'publishing' else 'pending',
                                    'phase':row['phase']}

    @staticmethod
    def _save_plan(objects, request_id, phase, plan):
        with objects.transaction() as conn:
            conn.execute('UPDATE shared_asset_publications SET phase=?, plan=? WHERE request_id=?',
                         (phase, json.dumps(plan), request_id))

    async def publish(self, body):
        request = AssetPublicationRequest.model_validate(deepcopy(body))
        digest = hashlib.sha256(fingerprint({'project_id':self.ctx.project_id, 'request':request.model_dump(mode='json')}).encode()).hexdigest()
        async with asset_project_lock(self.state_dir):
            row = self._row(request.request_id)
            if row:
                if row['actor_id'] != self.ctx.requester_user_id or row['request_hash'] != digest:
                    fail('idempotency_conflict', '请求编号已用于不同的保存操作')
                completed = self._result(row)
                if completed:
                    return completed
            if row is None:
                # Validate the complete, read-only input contract before any
                # registration, source creation, or durable request mutation.
                if request.mode != 'new_base':
                    entries = await registration_io(self.references.catalog,
                                                    selection=request.selection.model_dump())
                    if (len(entries) != 1 or entries[0]['proof'] != request.proof
                            or entries[0]['asset_ref'] != request.expected_ref.model_dump()):
                        fail('asset_selection_stale', '资产选择、预览与当前注册版本不一致，请重新选择')
                evidence = await registration_io(self.inspect_source, request.source.canvas_id, request.source.node_id)
                if evidence['proof'] != request.source.proof:
                    fail('asset_source_changed', '画布素材已变化，请重新选择')
                self._metadata(request)
                await registration_io(self._preflight_new_name, request)
                selection = request.selection.model_dump() if request.selection else {
                    'kind':request.kind, 'source_name':request.name, 'parent_source_name':''}
                if request.mode == 'new_variant':
                    selection = {**selection, 'source_name':request.name,
                                 'parent_source_name':selection['source_name']}
                jobs, bundle = self._jobs(request, evidence, selection)
                from novelvideo.freezone.slots import validate_source_for_slot
                for slot, url in jobs:
                    validate_source_for_slot(self._url_path(url), slot)
                if bundle:
                    for item in bundle['bundle_sources']:
                        for field, url in item['media'].items():
                            self._bundle_path(item, field, url)
            objects = self._init()
            source = SQLiteStore('shared-publication', output_dir=str(self.root), state_dir=str(self.state_dir))
            try:
                return await self._publish(source, objects, request, digest, row)
            finally:
                await source.close()

    async def _publish(self, source, objects, request, digest, row):
        from novelvideo.freezone.slots import validate_source_for_slot
        from .asset_canvas_publication import canvas_asset_publication, copy_canvas_publication, update_canvas_publication_source
        key = self._key(request.request_id)
        if row and row['phase'] == 'publishing':
            publication = MediaPublication(SourceSync(source, objects), self.ctx.requester_user_id)
            tokens = {entry['token'] for entry in publication.entries().values() if entry['request_key'] == key}
            for token in tokens:
                await publication.reconcile(token)
            completed = self._result(row)
            if completed:
                return completed
        evidence = await registration_io(self.inspect_source, request.source.canvas_id, request.source.node_id)
        if evidence['proof'] != request.source.proof:
            fail('asset_source_changed', '画布素材已变化，请重新选择')
        adapter = AssetAdapter(source, objects, self.ctx.requester_user_id)
        if row is None:
            if request.mode != 'new_base':
                ref = await self.references.register(request.selection.model_dump(), request.proof, actor=self.ctx.requester_user_id)
                if ref != request.expected_ref:
                    fail('asset_selection_stale', '资产版本已变化，请重新选择')
            self._metadata(request)
            plan = {'source':evidence}
            if request.mode != 'new_base':
                plan['selected_payload'] = self.references.resolve(request.expected_ref.model_dump())['object']['payload']
            with objects.transaction() as conn:
                conn.execute('INSERT INTO shared_asset_publications VALUES (?,?,?,?,?,?,NULL)',
                    (request.request_id, self.ctx.requester_user_id, digest, request.model_dump_json(), 'prepared', json.dumps(plan)))
                conn.execute('INSERT INTO asset_media_receipts VALUES (?,NULL)', (key,))
            _checkpoint('prepared')
        else:
            plan = json.loads(row['plan'])
        if 'registered_ref' not in plan:
            if request.mode != 'new_base':
                await self._validate_unregistered_target(objects, request, plan)
            if request.mode == 'new_base':
                await source.initialize()
                await registration_io(self._create_base, source, request, digest)
                _checkpoint('source_created')
                ref = await adapter.register_base(request.kind, request.name)
            elif request.mode == 'new_variant':
                await source.load_graph_state()
                ref = await adapter.create_variant(request.expected_ref.base_asset_id, request.name, request.metadata,
                                                   'shared-variant:' + request.request_id)
                _checkpoint('source_created')
            else:
                ref = request.expected_ref
            mapping = adapter._mapping(ref.variant_id or ref.base_asset_id)
            parent = adapter._mapping(mapping['parent_id'])['source_name'] if mapping['parent_id'] else ''
            selection = {'kind':mapping['kind'], 'source_name':mapping['source_name'], 'parent_source_name':parent}
            entries = await registration_io(self.references.catalog, selection=selection)
            if len(entries) != 1:
                fail('asset_source_changed', '保存目标已变化')
            plan.update(registered_ref=ref.model_dump(), selection=selection, registered_proof=entries[0]['proof'])
            self._save_plan(objects, request.request_id, 'registered', plan)
            _checkpoint('registered')
        ref = AssetRef.model_validate(plan['registered_ref'])
        current = objects.read(ref.variant_id or ref.base_asset_id)
        entries = await registration_io(self.references.catalog, selection=plan['selection'])
        if current['revision'] != ref.revision or len(entries) != 1 or entries[0]['proof'] != plan['registered_proof']:
            fail('asset_selection_stale', '保存目标已变化，不能重新覆盖')
        jobs, bundle = self._jobs(request, plan['source'], plan['selection'])
        async with AsyncExitStack() as stack:
            publications = []
            for slot, url in jobs:
                path = self._url_path(url)
                validate_source_for_slot(path, slot)
                publication = await stack.enter_async_context(canvas_asset_publication(self.ctx, self.root, slot, path, publish=False))
                expected = plan['source']['files'][url]
                if await registration_io(_digest, publication.source) != (expected['sha256'], expected['size']):
                    fail('asset_source_changed', '画布素材在准备期间发生变化，未写入资产')
                publications.append(publication)
            if bundle is not None:
                extras = await self._prepare_bundle(bundle, plan['source'], plan['selection']['source_name'])
                publications.extend(extras)
            latest = await registration_io(self.inspect_source, request.source.canvas_id, request.source.node_id)
            if latest['proof'] != request.source.proof:
                fail('asset_source_changed', '画布节点在准备期间发生变化，未写入资产')
            selection = plan['selection']
            slot_id = (f"character:{selection['parent_source_name']}:state:{selection['source_name']}" if selection['parent_source_name']
                       else f"character:{selection['source_name']}:portrait") if request.kind == 'character' else f"scene:{selection['source_name']}:base:master"
            self._save_plan(objects, request.request_id, 'publishing', plan)
            async with media_publication(source, [slot_id], key, self.ctx.requester_user_id):
                for publication in publications:
                    await copy_canvas_publication(self.ctx, self.root, publication)
                    await update_canvas_publication_source(publication, self.root)
                if bundle is not None:
                    self._write_bundle(selection['source_name'], bundle, publications[0], request.role)
                if request.mode == 'existing_version' and request.metadata:
                    await self._update_metadata(source, request, selection)
                _checkpoint('media_written')
            _checkpoint('semantic_committed')
        row = self._row(request.request_id)
        result = self._result(row)
        if not result:
            raise SourceSyncPending('Publication finished without a durable receipt')
        with objects.transaction() as conn:
            conn.execute("UPDATE shared_asset_publications SET phase='applied', result=? WHERE request_id=?", (json.dumps(result), request.request_id))
        _checkpoint('applied')
        return result

    @staticmethod
    def _metadata(request):
        if request.name:
            validate_path_segment(request.name, label='asset name')
        allowed = BASE_FIELDS[request.kind]
        if request.mode == 'new_variant' or request.selection and request.selection.parent_source_name:
            allowed = {'age_group','appearance_details','face_prompt'} if request.kind == 'character' else {'time_of_day','variant_prompt','description','notes'}
        if not set(request.metadata) <= allowed:
            fail('invalid_contract', '元数据字段不符合资产类型')

    async def _validate_unregistered_target(self, objects, request, plan):
        """A resumed create cannot quietly replace the original base selection.

        Character variant creation itself appends one identity to its parent.
        Its durable adapter receipt proves that exact, and only that, change.
        """
        entries = await registration_io(self.references.catalog, selection=request.selection.model_dump())
        if len(entries) != 1:
            fail('asset_selection_stale', '所选资产已变化')
        entry = entries[0]
        owned_child = None
        if request.mode == 'new_variant':
            from novelvideo.models import CharacterIdentity, NovelScene
            with objects.transaction() as conn:
                saved = conn.execute('SELECT * FROM asset_variant_requests WHERE request_id=?',
                                     ('shared-variant:' + request.request_id,)).fetchone()
            if saved:
                expected_input = json.dumps([request.expected_ref.base_asset_id,request.name,request.metadata],sort_keys=True,ensure_ascii=False)
                if saved['fingerprint'] != expected_input:
                    fail('idempotency_conflict', '变体请求所有权不匹配')
                owned_child = saved['source_name']
                child_selection = {'kind':request.kind, 'source_name':owned_child,
                                   'parent_source_name':request.selection.source_name}
                children = await registration_io(self.references.catalog, selection=child_selection)
                if children:
                    base = plan['selected_payload']
                    if request.kind == 'character':
                        expected_child = CharacterIdentity(identity_id=owned_child,character_name=base['name'],identity_name=request.name,
                            **{'age_group':base.get('age_group',''), **request.metadata})
                    else:
                        expected_child = NovelScene(name=owned_child,base_scene_id=base['name'],variant_id=request.name,
                                                   scene_type=base['scene_type'],**request.metadata)
                    actual_child = {k:v for k,v in children[0]['snapshot'].items() if k not in {'_media','_scene_manifest'}}
                    if len(children) != 1 or fingerprint(actual_child) != fingerprint(expected_child.model_dump(mode='json')) or children[0]['snapshot'].get('_media'):
                        fail('asset_selection_stale', '本请求创建的变体已被外部修改，需对账')
        if entry['proof'] == request.proof and entry['asset_ref'] == request.expected_ref.model_dump():
            return
        if request.mode == 'new_variant' and request.kind == 'character' and owned_child:
            current = deepcopy(entry['snapshot'])
            identities = json.loads(current.get('identities_json', '[]'))
            matches = [i for i in identities if i.get('identity_id') == owned_child]
            if len(matches) == 1:
                current['identities_json'] = json.dumps([i for i in identities if i.get('identity_id') != owned_child], ensure_ascii=False)
                expected = deepcopy(plan['selected_payload'])
                expected['identities_json'] = json.dumps(json.loads(expected.get('identities_json', '[]')), ensure_ascii=False)
                actual_ref = entry['asset_ref']
                if actual_ref and actual_ref['revision'] == request.expected_ref.revision + 1 and fingerprint(current) == fingerprint(expected):
                    return
        fail('asset_selection_stale', '基础资产在中断期间已变化，不能按新版本继续创建')

    def _preflight_new_name(self, request):
        """Mirror existing locator rules without creating schema or request state.

        The caller retains the project lock through the eventual INSERT. Only
        fresh requests use this check: owned interrupted sources and completed
        receipts must continue through their existing recovery paths.
        """
        if request.mode == 'existing_version':
            return
        if request.mode == 'new_variant' and request.kind == 'character':
            # Character variants own generated stable IDs. Their display names
            # are not unique in AssetAdapter.create_variant.
            return
        locator = request.name
        if request.mode == 'new_variant':
            from novelvideo.utils.derived_scenes import compose_derived_scene_name
            locator = compose_derived_scene_name(request.selection.source_name, request.name)
        db_path = self.state_dir/'data.db'
        if not db_path.is_file():
            return
        from .asset_rename import assert_asset_locators_writable, assert_no_pending_rename
        guard_source = SimpleNamespace(db_path=db_path)
        assert_no_pending_rename(guard_source, kind=request.kind, names={locator})
        assert_asset_locators_writable(guard_source, request.kind, {locator})
        table_name = 'characters' if request.kind == 'character' else 'scenes'
        with readonly(db_path) as conn:
            if not table(conn, table_name):
                return
            records = conn.execute(f'SELECT name,aliases_json FROM {table_name}').fetchall()
            if any(locator == row['name'] or request.mode == 'new_base'
                   and locator in json.loads(row['aliases_json'] or '[]') for row in records):
                fail('asset_name_exists', '此名称已存在，请选择现有资产或使用新名称')

    def _create_base(self, source, request, digest):
        """INSERT-only source and request ownership are one source DB transaction."""
        from .asset_rename import assert_asset_locators_writable, assert_no_pending_rename
        validate_path_segment(request.name, label='asset name')
        assert_no_pending_rename(source, kind=request.kind, names={request.name})
        assert_asset_locators_writable(source, request.kind, {request.name})
        table_name = 'characters' if request.kind == 'character' else 'scenes'
        with sqlite3.connect(source.db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('''CREATE TABLE IF NOT EXISTS shared_asset_source_owners (
                request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, kind TEXT NOT NULL,
                name TEXT NOT NULL, source_before TEXT NOT NULL, UNIQUE(kind,name))''')
            owner = conn.execute('SELECT * FROM shared_asset_source_owners WHERE request_id=?', (request.request_id,)).fetchone()
            existing = conn.execute(f'SELECT * FROM {table_name} WHERE name=?', (request.name,)).fetchone()
            if owner:
                if owner['request_hash'] != digest or existing is None or json.loads(owner['source_before']) != dict(existing):
                    fail('asset_source_changed', '中断创建的资产已被修改，需对账')
                return
            records = conn.execute(f'SELECT name,aliases_json FROM {table_name}').fetchall()
            if any(request.name in {r['name'], *json.loads(r['aliases_json'] or '[]')} for r in records):
                fail('asset_name_exists', '此名称已存在，请选择现有资产或使用新名称')
            values = {'name':request.name, **request.metadata}
            columns = list(values)
            conn.execute(f'INSERT INTO {table_name} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', list(values.values()))
            created = dict(conn.execute(f'SELECT * FROM {table_name} WHERE name=?', (request.name,)).fetchone())
            conn.execute('INSERT INTO shared_asset_source_owners VALUES (?,?,?,?,?)',
                         (request.request_id,digest,request.kind,request.name,json.dumps(created)))

    async def _update_metadata(self, source, request, selection):
        if request.kind == 'scene':
            await source.update_scene(selection['source_name'], **request.metadata)
        elif selection['parent_source_name']:
            await source.update_character_identity(selection['parent_source_name'], selection['source_name'], **request.metadata)
        else:
            await source.update_character(selection['source_name'], **request.metadata)

    def _jobs(self, request, evidence, selection):
        from novelvideo.freezone.slots import SlotTarget
        data = evidence['data']
        def slot(role):
            value = {'kind':role}
            if request.kind == 'scene':
                value['scene_id'] = selection['source_name']
            else:
                value['character'] = selection['parent_source_name'] or selection['source_name']
                if role != 'portrait':
                    if not selection['parent_source_name']:
                        fail('invalid_contract', '身份媒体需要选择具体变体')
                    value['identity_id'] = selection['source_name']
                elif selection['parent_source_name']:
                    fail('invalid_contract', '基础肖像不能写入变体')
            return TypeAdapter(SlotTarget).validate_python(value)
        if request.scene_bundle:
            return self._bundle_jobs(data, slot, request.role)
        field = 'plyUrl' if '3gs' in request.role else 'panoUrl' if request.role in {'scene_360','scene_director_pano_360'} else 'imageUrl'
        url = data.get(field)
        if not url:
            fail('asset_source_missing', '节点没有所选类型的媒体')
        return [(slot(request.role), url)], None

    def _bundle_jobs(self, data, slot, role):
        sources = deepcopy(data.get('sources') or [])
        if not sources:
            fail('invalid_contract', '完整场景保存需要明确的来源集合')
        bundle_sources = []
        for item in sources:
            source_type = item.get('source_type')
            if source_type not in {'sog', 'pano360', 'mesh'} or not isinstance(item.get('id'), str) or not item['id']:
                fail('invalid_contract', '场景来源须有唯一标识和类型')
            transform = item.get('transform')
            if transform is not None and (not isinstance(transform, dict)
                    or not set(transform) <= {'xOffset','yOffset','zOffset','yawDeg','pitchDeg','rollDeg','scale'}
                    or any(type(value) not in {int,float} or not math.isfinite(value) for value in transform.values())):
                fail('invalid_contract', '场景来源变换格式无效')
            primary_field = 'pano_url' if source_type == 'pano360' else 'ply_url'
            media = {key:item[key] for key in ('ply_url','pano_url','collision_glb_url','voxel_json_url') if item.get(key)}
            media.setdefault(primary_field, item.get('url'))
            if not media[primary_field]:
                fail('asset_source_missing', '场景来源缺少实际媒体')
            bundle_sources.append({key:deepcopy(item[key]) for key in ('id','source_type','source_kind','label','transform') if key in item} | {'media':media})
        ids = {item['id'] for item in bundle_sources}
        active = data.get('activeSourceId')
        if not isinstance(active, str) or not isinstance(data.get('scenesBySourceId', {}), dict):
            fail('invalid_contract', '场景来源与布局格式无效')
        snapshots = deepcopy(data.get('scenesBySourceId') or {})
        if len(ids) != len(bundle_sources) or active not in ids or not set(snapshots) <= ids:
            fail('invalid_contract', '场景布局与来源集合不匹配')
        if data.get('scene'):
            snapshots[active] = deepcopy(data['scene'])
        for key, snapshot in snapshots.items():
            if not isinstance(snapshot, dict):
                fail('invalid_contract', '场景布局格式无效')
            if not isinstance(snapshot.get('world', {}), dict):
                fail('invalid_contract', '场景布局的 world 格式无效')
            snapshot.setdefault('world', {})['activeSourceId'] = key
        selected = next(item for item in bundle_sources if item['id'] == active)
        panorama = selected['source_type'] == 'pano360'
        if panorama != (role in {'scene_360','scene_director_pano_360'}) or (not panorama and '3gs' not in role):
            fail('invalid_contract', '主槽位必须与当前场景来源媒体类型一致')
        if selected['source_type'] == 'mesh':
            fail('invalid_contract', '主槽位不支持网格类型；可保留为场景附加来源')
        selected_url = selected['media']['pano_url' if panorama else 'ply_url']
        manifest = data.get('assetSceneManifest') or {}
        if not isinstance(manifest, dict):
            fail('invalid_contract', '场景参数格式无效')
        return [(slot(role), selected_url)], {'bundle_sources':bundle_sources, 'active_source_id':active,
                'scenes_by_source_id':snapshots, 'pano_correction':deepcopy(manifest.get('pano_correction'))}

    def _bundle_path(self, item, field, url):
        path = self._url_path(url)
        allowed = {'.json'} if field == 'voxel_json_url' else {'.glb'} if field == 'collision_glb_url' or item['source_type'] == 'mesh' and field == 'ply_url' else {'.png','.jpg','.jpeg','.webp','.avif'} if field == 'pano_url' else {'.sog','.ply','.splat','.ksplat'}
        if path.suffix.lower() not in allowed:
            fail('asset_source_invalid', '场景附加来源文件类型不符合约定')
        return path

    async def _prepare_bundle(self, bundle, evidence, name):
        from .asset_canvas_publication import CanvasPublication, writable_path
        from .asset_media_publication import scene_mutation_path
        from novelvideo.director_world.stage_manifest import stage_dir
        publications = []
        for item in bundle['bundle_sources']:
            for field, url in item['media'].items():
                path = self._bundle_path(item, field, url)
                blob = await registration_io(capture_blob, self.root, path, archive=True)
                expected = evidence['files'][url]
                if (blob['sha256'],blob['size']) != (expected['sha256'],expected['size']):
                    fail('asset_source_changed', '场景附加来源在准备期间已变化')
                target = writable_path(self.root, stage_dir(self.root,name)/('shared-'+blob['sha256']+path.suffix.lower()))
                scene_mutation_path(self.root,name,target)
                publications.append(CanvasPublication(self.root/blob['path'], path, target,
                    SimpleNamespace(kind='scene_bundle_media', scene_id=name)))
                item['media'][field] = target.name
        return publications

    def _write_bundle(self, name, bundle, primary, role):
        from novelvideo.director_world import stage_manifest
        from .asset_media import SCENE_MANIFEST_PATHS
        from novelvideo.utils.static_urls import project_static_url
        bundle = deepcopy(bundle)
        sources = []
        for item in bundle['bundle_sources']:
            sources.append({key:value for key,value in item.items() if key != 'media'} | {
                field:project_static_url(self.ctx.project_id,(stage_manifest.stage_dir(self.root,name)/path).relative_to(self.root).as_posix())
                for field,path in item['media'].items()})
        active = next(item for item in sources if item['id'] == bundle['active_source_id'])
        field = {'scene_360':'pano_path','scene_director_pano_360':'pano_path', 'scene_3gs_custom_scene':'custom_scene_path',
                 'scene_3gs_master_ply':'master_ply_path','scene_3gs_reverse_ply':'reverse_ply_path',
                 'scene_3gs_pano_ply':'pano_ply_path','scene_3gs_active_ply':'ply_path'}[role]
        paths = {field:primary.target.name}
        # scene_360 lives outside the stage directory. The authoritative bundle
        # panorama is the verified scene-local additional copy in that case.
        selected = next(item for item in bundle['bundle_sources'] if item['id'] == bundle['active_source_id'])
        if field == 'pano_path':
            paths[field] = selected['media']['pano_url']
        else:
            paths['ply_path'] = primary.target.name
            for media_field, manifest_field in [('pano_url','pano_path'),('collision_glb_url','collision_glb_path'),('voxel_json_url','voxel_json_path')]:
                if media_field in selected['media']:
                    paths[manifest_field] = selected['media'][media_field]
        pruned = sorted(set(SCENE_MANIFEST_PATHS) - set(paths))
        # Replace the complete layout/source relation in one atomic manifest write.
        stage_manifest.update_manifest(self.root, name, **bundle, **paths, source='shared_publication',
            sources=sources, bundle_schema_version=1,
            bundle_primary_role='panorama_360' if role == 'scene_360' else field.removesuffix('_path'),
            bundle_primary_source_id=bundle['active_source_id'], active_source=active,
            clear_fields=pruned, pruned_media_fields=pruned,
            scene=bundle['scenes_by_source_id'].get(bundle['active_source_id'], {}))
