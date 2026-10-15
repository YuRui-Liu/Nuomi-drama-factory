"""Install the two CC0 Quaternius Standard assets with pinned content hashes."""
from pathlib import Path
from io import BytesIO
import hashlib
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1] / 'frontend/public/viewer-kit/quaternius'
ASSETS = [
    ('ual1/UAL1_Standard.glb', 'https://raw.githubusercontent.com/richardanaya/metaverse-avatar/master/anims/UAL1_Standard.glb', 'd867292451e432b735e2a910c2db6640fbea97b205d85a2e8ffed26da87972cf'),
    ('ual2/UAL2_Standard.glb', 'https://opengameart.org/sites/default/files/universal_animation_library_2standard.zip', '0815dd05531cae9bc313fc9c0ba81330bc72f8e19ec45f73738e74ddc5796a43'),
]

def main():
    for relative, url, expected in ASSETS:
        target = ROOT / relative
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
            print(f'Verified {relative}')
            continue
        with urllib.request.urlopen(url, timeout=45) as response:
            data = response.read(50 * 1024 * 1024)
        if url.endswith('.zip'):
            with zipfile.ZipFile(BytesIO(data)) as archive:
                matches = [n for n in archive.namelist() if n.endswith('/Unreal-Godot/UAL2_Standard.glb')]
                if len(matches) != 1:
                    raise ValueError('Expected exactly one UAL2 Standard GLB')
                data = archive.read(matches[0])
        if data[:4] != b'glTF' or hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f'Asset validation failed: {relative}')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        print(f'Installed {relative}: {len(data)} bytes, SHA-256 {expected}')

if __name__ == '__main__':
    main()
