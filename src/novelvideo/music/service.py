"""Music runtime dependencies and public media views."""
from pathlib import Path
from urllib.parse import quote

from novelvideo import config
from .store import MusicStore


def get_store():
    return MusicStore(Path(config.STATE_DIR) / 'local' / 'music.db')


def media_root():
    return Path(config.OUTPUT_DIR) / '.music'


def public_asset(asset, project=None):
    result = {k: v for k, v in asset.items() if k not in {'path'}}
    version = quote(asset['versionId'], safe='')
    result['url'] = (f'/api/v1/projects/{quote(project, safe="")}/music/versions/{version}/media' if project
                     else f'/api/v1/music-library/versions/{version}/audio')
    return result
