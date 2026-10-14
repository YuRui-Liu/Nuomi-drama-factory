"""Explicit project definition/creative-agreement API; no Agent authority from JSON."""
import asyncio
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends

from novelvideo.api.auth import get_api_user
from novelvideo.creation.ontology import Ontology
from novelvideo.creation.creative_constraints import CreativeConstraints
from novelvideo.creation.agent_profiles import AgentProfiles
from novelvideo.creation.creative_templates import CreativeTemplates
from novelvideo.creation.personal_library import PersonalLibrary
from .creation import context_for, errors

router = APIRouter(prefix='/projects/{project}/creation', tags=['creation-library'])


LibraryKind=Literal['agent','template']


async def reusable_service(project,user,kind,role='viewer'):
    from novelvideo import config
    ctx=await context_for(project,user,role)
    library=PersonalLibrary(Path(config.STATE_DIR)/'creation-libraries',ctx.requester_user_id)
    return (AgentProfiles if kind=='agent' else CreativeTemplates)(ctx,library)


@router.get('/reusable/capabilities')
async def reusable_capabilities(project: str,user: dict=Depends(get_api_user)):
    with errors():
        await context_for(project,user,'viewer')
        return {'ok':True,'data':AgentProfiles.capabilities()}


@router.get('/reusable/studio-resources')
async def reusable_studio_resources(project: str,user: dict=Depends(get_api_user)):
    with errors():
        service=await reusable_service(project,user,'agent')
        return {'ok':True,'data':await asyncio.to_thread(service.studio_resources)}


@router.get('/reusable/{kind}/personal')
async def personal_catalog(project: str,kind: LibraryKind,user: dict=Depends(get_api_user)):
    with errors():
        service=await reusable_service(project,user,kind)
        return {'ok':True,'data':await asyncio.to_thread(service.library.catalog,kind)}


@router.get('/reusable/{kind}/personal/versions/{identifier}/{revision}')
async def personal_version(project: str,kind: LibraryKind,identifier: str,revision: int,user: dict=Depends(get_api_user)):
    with errors():
        service=await reusable_service(project,user,kind)
        return {'ok':True,'data':await asyncio.to_thread(service.library.version,kind,{'id':identifier,'revision':revision})}


@router.get('/reusable/{kind}/personal/requests/{request_id}')
async def personal_request(project: str,kind: LibraryKind,request_id: str,user: dict=Depends(get_api_user)):
    with errors():
        service=await reusable_service(project,user,kind)
        result=await asyncio.to_thread(service.library.receipt,request_id)
        return {'ok':True,'data':result or {'request_id':request_id,'status':'unknown'}}


@router.post('/reusable/{kind}/preview')
async def reusable_preview(project: str,kind: LibraryKind,body: dict,user: dict=Depends(get_api_user)):
    with errors():
        from novelvideo.creation.ontology import exact
        exact(body,{'configuration'})
        service=await reusable_service(project,user,kind)
        return {'ok':True,'data':await asyncio.to_thread(service.preview,body['configuration'])}


@router.post('/reusable/{kind}/personal/operations')
async def personal_operation(project: str,kind: LibraryKind,body: dict,user: dict=Depends(get_api_user)):
    with errors():
        service=await reusable_service(project,user,kind,'editor')
        return {'ok':True,'data':await service.save(body)}


@router.post('/reusable/{kind}/personal/patch-preview')
async def personal_patch_preview(project: str,kind: LibraryKind,body: dict,user: dict=Depends(get_api_user)):
    with errors():
        from novelvideo.creation.ontology import exact
        exact(body,{'ref','changes'})
        service=await reusable_service(project,user,kind)
        return {'ok':True,'data':await asyncio.to_thread(service.personal_patch_preview,body['ref'],body['changes'])}


@router.get('/reusable/{kind}/adoptions')
async def adoption_catalog(project: str,kind: LibraryKind,user: dict=Depends(get_api_user)):
    with errors():
        service=await reusable_service(project,user,kind)
        return {'ok':True,'data':await asyncio.to_thread(service.catalog)}


@router.get('/reusable/{kind}/adoptions/{identifier}')
async def adopted_version(project: str,kind: LibraryKind,identifier: str,revision: int|None=None,user: dict=Depends(get_api_user)):
    with errors():
        service=await reusable_service(project,user,kind)
        return {'ok':True,'data':await asyncio.to_thread(service.adoption,identifier,revision)}


@router.post('/reusable/{kind}/adoption-preview')
async def adoption_preview(project: str,kind: LibraryKind,body: dict,user: dict=Depends(get_api_user)):
    with errors():
        from novelvideo.creation.ontology import exact
        exact(body,{'ref'},{'definition_names'})
        service=await reusable_service(project,user,kind)
        if kind=='template':
            result=await asyncio.to_thread(service.adoption_preview,body['ref'],body.get('definition_names'))
        else:
            result=await asyncio.to_thread(service.library.version,kind,body['ref'])
        return {'ok':True,'data':result}


@router.post('/reusable/{kind}/upgrade-preview')
async def upgrade_preview(project: str,kind: LibraryKind,body: dict,user: dict=Depends(get_api_user)):
    with errors():
        from novelvideo.creation.ontology import exact
        exact(body,{'adoption_id','ref'},{'definition_names'})
        service=await reusable_service(project,user,kind)
        return {'ok':True,'data':await asyncio.to_thread(service.upgrade_preview,body['adoption_id'],body['ref'],body.get('definition_names'))}


@router.post('/reusable/{kind}/override-preview')
async def override_preview(project: str,kind: LibraryKind,body: dict,user: dict=Depends(get_api_user)):
    with errors():
        from novelvideo.creation.ontology import exact
        exact(body,{'adoption_id','changes'})
        service=await reusable_service(project,user,kind)
        return {'ok':True,'data':await asyncio.to_thread(service.override_preview,body['adoption_id'],body['changes'])}


@router.post('/reusable/{kind}/operations')
async def adoption_operation(project: str,kind: LibraryKind,body: dict,user: dict=Depends(get_api_user)):
    with errors():
        service=await reusable_service(project,user,kind,'editor')
        return {'ok':True,'data':await service.apply(body)}


@router.get('/reusable/template/activation-preview/{identifier}')
async def template_activation_preview(project: str,identifier: str,user: dict=Depends(get_api_user)):
    with errors():
        service=await reusable_service(project,user,'template')
        return {'ok':True,'data':await asyncio.to_thread(service.activation_preview,identifier)}


@router.get('/reusable/{kind}/requests/{request_id}')
async def adoption_request(project: str,kind: LibraryKind,request_id: str,user: dict=Depends(get_api_user)):
    with errors():
        service=await reusable_service(project,user,kind)
        result=await asyncio.to_thread(service.receipt,request_id)
        return {'ok':True,'data':result or {'request_id':request_id,'status':'unknown'}}


@router.get('/ontology')
async def ontology_catalog(project: str, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project,user,'viewer')
        return {'ok':True,'data':await asyncio.to_thread(Ontology(ctx).catalog)}


@router.get('/ontology/definitions/{name}')
async def ontology_definition(project: str, name: str, revision: int | None = None, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project,user,'viewer')
        return {'ok':True,'data':await asyncio.to_thread(Ontology(ctx).definition,name,revision)}


@router.get('/ontology/objects/{object_id}')
async def ontology_object(project: str, object_id: str, revision: int | None = None, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project,user,'viewer')
        return {'ok':True,'data':await asyncio.to_thread(Ontology(ctx).object,object_id,revision)}


@router.get('/ontology/objects')
async def ontology_objects(project: str, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project,user,'viewer')
        return {'ok':True,'data':await asyncio.to_thread(Ontology(ctx).object_catalog)}


@router.get('/ontology/relations')
async def ontology_relations(project: str, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project,user,'viewer')
        return {'ok':True,'data':await asyncio.to_thread(Ontology(ctx).relation_catalog)}


@router.get('/ontology/relations/{relation_id}')
async def ontology_relation(project: str, relation_id: str, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project,user,'viewer')
        return {'ok':True,'data':await asyncio.to_thread(Ontology(ctx).relation,relation_id)}


@router.post('/ontology/operations')
async def ontology_operation(project: str, body: dict, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project,user,'editor')
        # This is the explicit user-command endpoint. Agent tools must invoke the
        # service with its default agent authority, never proxy this confirmation.
        return {'ok':True,'data':await Ontology(ctx,authority='user').apply(body)}


@router.get('/ontology/requests/{request_id}')
async def ontology_receipt(project: str, request_id: str, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project,user,'viewer')
        result=await asyncio.to_thread(Ontology(ctx).receipt,request_id)
        return {'ok':True,'data':result or {'request_id':request_id,'status':'unknown'}}


@router.get('/constraints')
async def constraint_catalog(project: str, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project,user,'viewer')
        return {'ok':True,'data':await asyncio.to_thread(CreativeConstraints(ctx).catalog)}


@router.get('/constraints/effective')
async def effective_constraints(project: str, level: str = 'project', canvas_id: str | None = None,
        group_id: str | None = None, node_id: str | None = None, object_id: str | None = None,
        episode_id: str | None = None, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project,user,'viewer')
        scope={key:value for key,value in {'level':level,'canvas_id':canvas_id,'group_id':group_id,
            'node_id':node_id,'object_id':object_id,'episode_id':episode_id}.items() if value is not None}
        return {'ok':True,'data':await asyncio.to_thread(CreativeConstraints(ctx).resolve,scope)}


@router.get('/constraints/records/{constraint_id}')
async def constraint_record(project: str, constraint_id: str, revision: int | None = None, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project,user,'viewer')
        return {'ok':True,'data':await asyncio.to_thread(CreativeConstraints(ctx).record,constraint_id,revision)}


@router.get('/constraints/proposals/{proposal_id}')
async def constraint_proposal(project: str, proposal_id: str, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project,user,'viewer')
        return {'ok':True,'data':await asyncio.to_thread(CreativeConstraints(ctx).proposal,proposal_id)}


@router.post('/constraints/operations')
async def constraint_operation(project: str, body: dict, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project,user,'editor')
        return {'ok':True,'data':await CreativeConstraints(ctx,authority='user').apply(body)}


@router.get('/constraints/requests/{request_id}')
async def constraint_receipt(project: str, request_id: str, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project,user,'viewer')
        result=await asyncio.to_thread(CreativeConstraints(ctx).receipt,request_id)
        return {'ok':True,'data':result or {'request_id':request_id,'status':'unknown'}}
