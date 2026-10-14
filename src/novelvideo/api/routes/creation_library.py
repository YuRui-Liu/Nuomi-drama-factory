"""Explicit project definition/creative-agreement API; no Agent authority from JSON."""
import asyncio

from fastapi import APIRouter, Depends

from novelvideo.api.auth import get_api_user
from novelvideo.creation.ontology import Ontology
from novelvideo.creation.creative_constraints import CreativeConstraints
from .creation import context_for, errors

router = APIRouter(prefix='/projects/{project}/creation', tags=['creation-library'])


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
