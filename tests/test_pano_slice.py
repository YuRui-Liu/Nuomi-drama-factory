from PIL import Image

from novelvideo.creation.asset_design.pano import slice_pano


def test_slice_is_deterministic(tmp_path):
    pano = Image.new("RGB", (2048, 1024), (30, 60, 90))
    src = tmp_path / "pano.png"
    pano.save(src)
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    slice_pano(src, a, yaw=120, pitch=0, fov=60, width=512, height=384)
    slice_pano(src, b, yaw=120, pitch=0, fov=60, width=512, height=384)
    assert a.read_bytes() == b.read_bytes()
    assert Image.open(a).size == (512, 384)
