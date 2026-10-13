import pytest
from pydantic import ValidationError

from novelvideo.creation.models import AssetRef, OperationRequest, OperationResult, Scope


@pytest.mark.parametrize('model,kwargs', [
    (AssetRef, dict(base_asset_id='', revision=1)),
    (AssetRef, dict(base_asset_id='a', revision=0)),
    (Scope, dict(project_id='', episode_id='e', canvas_id='c')),
    (OperationRequest, dict(request_id='x' * 129, kind='object.create', scope=dict(project_id='p', episode_id='e', canvas_id='c'), payload={})),
    (OperationRequest, dict(request_id='r', kind='unknown', scope=dict(project_id='p', episode_id='e', canvas_id='c'), payload={})),
    (OperationResult, dict(operation_id='o', status='unknown')),
])
def test_invalid_fields(model, kwargs):
    with pytest.raises(ValidationError):
        model(**kwargs)


@pytest.mark.parametrize('model,kwargs', [
    (AssetRef, dict(base_asset_id='a', revision=1)),
    (Scope, dict(project_id='p', episode_id='e', canvas_id='c')),
    (OperationRequest, dict(request_id='r', kind='object.create', scope=dict(project_id='p', episode_id='e', canvas_id='c'), payload={})),
    (OperationResult, dict(operation_id='o', status='applied')),
])
def test_unknown_fields_forbidden(model, kwargs):
    with pytest.raises(ValidationError):
        model(**kwargs, unexpected=True)


def test_default_lists_are_independent():
    a = Scope(project_id='p', episode_id='e', canvas_id='c')
    b = Scope(project_id='p', episode_id='e', canvas_id='c')
    a.node_ids.append('n')
    assert b.node_ids == []
