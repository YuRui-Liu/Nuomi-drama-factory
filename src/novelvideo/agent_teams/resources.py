"""Content identity shared by resource publishers and snapshot consumers."""
import hashlib

from .models import ResourceVersion


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def verify_resource(resource: ResourceVersion) -> ResourceVersion:
    resource = ResourceVersion.model_validate(resource.model_dump(mode='json'))
    if resource.content_hash != content_hash(resource.content):
        raise ValueError('resource content hash mismatch')
    return resource
