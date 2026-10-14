"""Private immutable library versions and independent project adoptions.

Library content describes configuration, never runtime/tool authorization.
"""
import json
import sqlite3
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from .asset_reference import readonly, table
from .library_references import digest, packed
from .ontology import DomainCommand, ProjectDomain, exact, integer, require, text


def checkpoint(phase):
    """Crash-injection boundary; receipts commit with their content."""


class PersonalLibrary:
    def __init__(self, root, user_id):
        require(isinstance(user_id,str) and bool(user_id.strip()),'缺少认证用户','scope_violation')
        self.root=Path(root)
        self.user_id=user_id
        self.directory=self.root/sha256(user_id.encode()).hexdigest()
        self.db=self.directory/'library.sqlite3'

    def _paths(self):
        require(not self.root.is_symlink() and not self.directory.is_symlink() and not self.db.is_symlink(),
                '个人库路径无效','scope_violation')
        require(self.db.resolve().is_relative_to(self.root.resolve()),'个人库路径越界','scope_violation')

    def _identity(self,conn):
        row=conn.execute('SELECT user_id FROM library_owner').fetchone()
        require(row is not None and row[0]==self.user_id,'个人库身份不匹配','scope_violation')

    @contextmanager
    def read(self):
        self._paths()
        if not self.db.exists():
            yield None
        else:
            with readonly(self.db) as conn:
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                    # A rolled-back first save may leave SQLite's empty file.
                    yield None
                    return
                require(table(conn,'library_owner'),'个人库缺少身份记录','scope_violation')
                self._identity(conn)
                yield conn

    @staticmethod
    def _kind(kind):
        require(kind in {'agent','template'},'未知个人库类型')

    def _version(self,conn,kind,ref):
        self._kind(kind)
        exact(ref,{'id','revision'})
        text(ref['id'],100);integer(ref['revision'],1)
        row=conn.execute('SELECT content FROM library_versions WHERE kind=? AND id=? AND revision=?',
                         (kind,ref['id'],ref['revision'])).fetchone() if conn else None
        require(row is not None,'个人版本不存在或不属于当前用户','scope_violation')
        return json.loads(row[0])

    def version(self,kind,ref):
        with self.read() as conn:
            return self._version(conn,kind,ref)

    def catalog(self,kind):
        self._kind(kind)
        with self.read() as conn:
            if conn is None:
                return []
            return [json.loads(r[0]) for r in conn.execute('''SELECT v.content FROM library_versions v
                JOIN (SELECT id,MAX(revision) revision FROM library_versions WHERE kind=? GROUP BY id) c
                ON v.id=c.id AND v.revision=c.revision WHERE v.kind=? ORDER BY v.rowid''',(kind,kind))]

    def receipt(self,request_id):
        with self.read() as conn:
            row=conn.execute('SELECT result FROM library_receipts WHERE request_id=?',(request_id,)).fetchone() if conn else None
            return json.loads(row[0]) if row else None

    def apply(self,kind,body,build,patch=None):
        self._kind(kind)
        request=DomainCommand.model_validate(body)
        fingerprint=digest([self.user_id,kind,request.model_dump(mode='json')])
        def replay(conn):
            row=conn.execute('SELECT * FROM library_receipts WHERE request_id=?',(request.request_id,)).fetchone() if conn else None
            if row:
                require(row['request_hash']==fingerprint,'请求编号已用于其他内容','idempotency_conflict')
                return json.loads(row['result'])
        with self.read() as conn:
            result=replay(conn)
            if result is not None:
                return result
        # Validate/build before creating any personal directories or tables.
        p=request.payload
        if request.action=='patch':
            exact(p,{'ref','changes','proof'})
            require(patch is not None,'此配置不支持字段修改')
            previous=self.version(kind,p['ref'])
            configuration=patch(previous,p['changes'],p['proof'])
            identifier=previous['id'];expected=previous['revision']
        elif request.action=='copy':
            exact(p,{'ref','name'})
            configuration={**self.version(kind,p['ref'])['configuration'],'name':text(p['name'],200)}
            identifier=uuid4().hex;expected=0
        else:
            require(request.action=='save','未知个人库操作')
            exact(p,{'configuration','proof','expected_revision'},{'id'})
            expected=integer(p['expected_revision'],0)
            require(('id' in p)==(expected>0),'新版本须提供当前个人版本')
            identifier=text(p['id'],100) if expected else uuid4().hex
            configuration=build(p['configuration'],p['proof'])
        require(len(packed(configuration).encode())<=2_000_000,'个人配置超过2MB限制')
        self._paths();self.directory.mkdir(parents=True,exist_ok=True)
        conn=sqlite3.connect(self.db,timeout=15);conn.row_factory=sqlite3.Row
        try:
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                conn.execute('CREATE TABLE IF NOT EXISTS library_owner (user_id TEXT NOT NULL)')
                if not conn.execute('SELECT 1 FROM library_owner').fetchone():
                    conn.execute('INSERT INTO library_owner VALUES (?)',(self.user_id,))
                self._identity(conn)
                conn.execute('''CREATE TABLE IF NOT EXISTS library_versions (kind TEXT,id TEXT,revision INTEGER,
                    content TEXT NOT NULL,PRIMARY KEY(kind,id,revision))''')
                conn.execute('''CREATE TABLE IF NOT EXISTS library_receipts (request_id TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,result TEXT NOT NULL)''')
                result=replay(conn)
                if result is not None:
                    return result
                current=conn.execute('SELECT MAX(revision) FROM library_versions WHERE kind=? AND id=?',(kind,identifier)).fetchone()[0] or 0
                require(current==expected,'个人库已有新版本，请比较后保存','revision_conflict')
                ref={'id':identifier,'revision':current+1}
                content={'kind':kind,**ref,'configuration':configuration}
                content['proof']=digest(content)
                conn.execute('INSERT INTO library_versions VALUES (?,?,?,?)',(kind,identifier,current+1,packed(content)))
                conn.execute('INSERT INTO library_receipts VALUES (?,?,?)',(request.request_id,fingerprint,packed(ref)))
                checkpoint('before_commit')
            checkpoint('after_commit')
            return ref
        finally:
            conn.close()


_ADOPTION_SCHEMA=(
    '''CREATE TABLE IF NOT EXISTS creative_adoptions (id TEXT PRIMARY KEY,project_id TEXT,kind TEXT,current_revision INTEGER)''',
    '''CREATE TABLE IF NOT EXISTS creative_adoption_versions (id TEXT,revision INTEGER,content TEXT,
        PRIMARY KEY(id,revision),FOREIGN KEY(id) REFERENCES creative_adoptions(id))''',
)


class ProjectLibrary(ProjectDomain):
    schema=_ADOPTION_SCHEMA
    kind='agent'
    editable=frozenset()

    def __init__(self,ctx,library):
        super().__init__(ctx,authority='user')
        require(library.user_id==ctx.requester_user_id,'个人库身份与操作者不匹配','scope_violation')
        self.library=library

    def _adoption(self,conn,identifier,revision=None):
        row=conn.execute('''SELECT v.content FROM creative_adoptions a JOIN creative_adoption_versions v
            ON a.id=v.id AND v.revision=COALESCE(?,a.current_revision)
            WHERE a.id=? AND a.project_id=? AND a.kind=?''',
            (revision,identifier,self.ctx.project_id,self.kind)).fetchone() if conn and table(conn,'creative_adoptions') else None
        require(row is not None,'项目采用不存在或不属于当前项目','scope_violation')
        return json.loads(row[0])

    def adoption(self,identifier,revision=None):
        with self.read() as conn:
            return self._adoption(conn,identifier,revision)

    def catalog(self):
        with self.read() as conn:
            if conn is None or not table(conn,'creative_adoptions'):
                return []
            return [self._adoption(conn,row[0]) for row in conn.execute(
                'SELECT id FROM creative_adoptions WHERE project_id=? AND kind=?',(self.ctx.project_id,self.kind))]

    @staticmethod
    def _effective(content):
        return {**content['fixed']['configuration'],**{k:v['value'] for k,v in content['overrides'].items()}}

    def upgrade_preview(self,identifier,ref,definition_names=None):
        old=self.adoption(identifier)
        new=self.library.version(self.kind,ref)
        return self._upgrade_preview(old,new,definition_names)

    def _upgrade_preview(self,old,new,definition_names=None):
        require(old['fixed']['id']==new['id'],'升级必须选择同一库条目的版本')
        before=old['fixed']['configuration'];after=new['configuration']
        conflicts=[key for key,item in old['overrides'].items()
                   if item['base']!=after.get(key) and item['value']!=after.get(key)]
        preview={'adoption_id':old['adoption_id'],'expected_revision':old['revision'],
                 'before':before,'after':after,'overrides':old['overrides'],'conflicts':conflicts,
                 'dependencies_before':self.dependencies(before),'dependencies_after':self.dependencies(after),
                 'ref':{'id':new['id'],'revision':new['revision']}}
        preview['proof']=digest(preview)
        return preview

    def dependencies(self,configuration):
        return configuration.get('defaults',{})

    def install(self,conn,content,operation_id,payload):
        return content

    def validate_adoption_proof(self,conn,fixed,payload):
        require(fixed['proof']==payload['proof'],'所选个人版本已变化','source_conflict')

    def validate_override(self,changes):
        require(isinstance(changes,dict) and set(changes)<=self.editable,'覆盖字段不允许')
        for value in changes.values():
            if value is not None:
                text(value,12000)
        return changes

    def override_preview(self,identifier,changes):
        return self._override_preview(self.adoption(identifier),changes)

    def personal_patch_preview(self,ref,changes):
        fixed=self.library.version(self.kind,ref)
        values=self.validate_override(changes)
        require(all(v is not None for v in values.values()),'个人字段修改请提供明确值，清空列表使用空列表')
        content={**fixed['configuration'],**values}
        return {'configuration':content,'proof':digest([fixed,content])}

    def build_patch(self,previous,changes,proof):
        preview=self.personal_patch_preview({'id':previous['id'],'revision':previous['revision']},changes)
        require(preview['proof']==proof,'个人修改预览已变化','source_conflict')
        return preview['configuration']

    def _override_preview(self,old,changes):
        values=self.validate_override(changes)
        preview={'adoption_id':old['adoption_id'],'expected_revision':old['revision'],
                 'changes':values,'before':old['effective']}
        preview['proof']=digest(preview)
        return preview

    def dispatch(self,conn,request,operation_id):
        p=request.payload
        if request.action=='adopt':
            exact(p,{'ref','proof'}, {'definition_names'})
            fixed=self.library.version(self.kind,p['ref'])
            self.validate_adoption_proof(conn,fixed,p)
            content={'adoption_id':uuid4().hex,'revision':1,'fixed':fixed,'overrides':{},
                     'source':{'user_id':self.library.user_id,'ref':p['ref']}}
            content=self.install(conn,content,operation_id,p)
            conn.execute('INSERT INTO creative_adoptions VALUES (?,?,?,1)',(content['adoption_id'],self.ctx.project_id,self.kind))
        else:
            require(request.action in {'override','upgrade'},'未知项目采用操作')
            required={'adoption_id','expected_revision'}
            exact(p,required|({'changes','proof'} if request.action=='override' else {'ref','proof','resolutions'}),
                  set() if request.action=='override' else {'definition_names'})
            old=self._adoption(conn,p['adoption_id'])
            require(old['revision']==integer(p['expected_revision'],1),'项目采用已变化','revision_conflict')
            content={**old,'revision':old['revision']+1,'overrides':dict(old['overrides'])}
            if request.action=='override':
                preview=self._override_preview(old,p['changes'])
                require(preview['proof']==p['proof'],'项目覆盖比较已变化','source_conflict')
                for key,value in preview['changes'].items():
                    if value is None:
                        content['overrides'].pop(key,None)
                    else:
                        content['overrides'][key]={'value':value,'base':old['fixed']['configuration'].get(key)}
            else:
                fixed=self.library.version(self.kind,p['ref'])
                preview=self._upgrade_preview(old,fixed,p.get('definition_names'))
                require(preview['proof']==p['proof'],'升级比较已过期','source_conflict')
                require(isinstance(p['resolutions'],dict) and set(p['resolutions'])==set(preview['conflicts'])
                        and all(v in {'keep','drop'} for v in p['resolutions'].values()),'须明确处理每项覆盖冲突','override_conflict')
                for key,choice in p['resolutions'].items():
                    if choice=='drop':
                        content['overrides'].pop(key,None)
                content['fixed']=fixed
                content['source']={'user_id':self.library.user_id,
                                   'ref':{'id':fixed['id'],'revision':fixed['revision']}}
                for key,item in content['overrides'].items():
                    item={**item,'base':fixed['configuration'].get(key)}
                    content['overrides'][key]=item
                content=self.install(conn,content,operation_id,p)
            conn.execute('UPDATE creative_adoptions SET current_revision=? WHERE id=?',(content['revision'],content['adoption_id']))
        content['effective']=self._effective(content)
        conn.execute('INSERT INTO creative_adoption_versions VALUES (?,?,?)',(content['adoption_id'],content['revision'],packed(content)))
        return {'adoption_id':content['adoption_id'],'revision':content['revision'],'effective':content['effective']}
