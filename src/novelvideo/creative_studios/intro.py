"""Local title compositor. Text never mutates the source background or episode."""
from __future__ import annotations

import json
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Literal

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import BaseModel, ConfigDict, Field


class IntroSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(default='我的故事', max_length=100)
    subtitle: str = Field(default='', max_length=180)
    background: str = ''
    font: str = ''
    color: str = Field(default='#ffffff', pattern=r'^#[0-9a-fA-F]{6}$')
    align: Literal['left', 'center', 'right'] = 'center'
    x: float = Field(default=.5, ge=0, le=1)
    y: float = Field(default=.5, ge=0, le=1)
    size: float = Field(default=.10, ge=.02, le=.25)
    duration: float = Field(default=4, ge=1, le=30)
    ratio: Literal['16:9', '9:16', '1:1'] = '16:9'
    effect: Literal['static', 'fade', 'typewriter', 'zoom', 'shine'] = 'fade'
    insert_video: str = ''


def fonts() -> list[dict]:
    candidates = [
        ('pingfang', '苹方', '/System/Library/Fonts/PingFang.ttc'),
        ('songti', '宋体', '/System/Library/Fonts/Supplemental/Songti.ttc'),
        ('noto-cjk', 'Noto Sans CJK', '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
        ('dejavu', 'DejaVu Sans（拉丁字符）', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        ('arial', 'Arial Unicode', '/System/Library/Fonts/Supplemental/Arial Unicode.ttf'),
    ]
    return [dict(id=id_, name=name, path=path) for id_, name, path in candidates if Path(path).is_file()]


def project_file(root: Path, name: str) -> Path:
    if Path(name).is_absolute():
        raise ValueError('素材必须为项目内相对路径')
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('素材路径越出项目目录')
    if not path.is_file():
        raise ValueError('引用素材不存在，请重新选择')
    return path


def render_frame(background: Image.Image, spec: IntroSpec, t: float) -> Image.Image:
    width, height = background.size
    available = fonts()
    selected = next((f for f in available if f['id'] == spec.font), None)
    if selected is None:
        raise ValueError('所选字体不可用，请重新选择实际已安装字体')
    progress = min(1., max(0., t / min(1.5, spec.duration * .6)))
    alpha = progress if spec.effect == 'fade' else 1.
    scale = .6 + .4 * (1 - (1 - progress) ** 3) if spec.effect == 'zoom' else 1.
    layer = Image.new('RGBA', background.size)
    draw = ImageDraw.Draw(layer)
    anchor = {'left': 'la', 'center': 'ma', 'right': 'ra'}[spec.align]
    title = spec.title
    if spec.effect == 'typewriter':
        title = title[:math.floor(len(title) * progress)]
    size = max(8, round(height * spec.size * scale))
    main_font = ImageFont.truetype(selected['path'], size)
    sub_font = ImageFont.truetype(selected['path'], max(8, round(size * .42)))
    x, y = round(width * spec.x), round(height * spec.y)
    draw.text((x, y), title, font=main_font, fill=spec.color, anchor=anchor, stroke_width=1, stroke_fill='#202020')
    draw.text((x, y + size * 1.45), spec.subtitle, font=sub_font, fill=spec.color, anchor=anchor)
    if spec.effect == 'shine':
        # A moving diagonal highlight clipped to the independent glyph alpha.
        light = Image.new('RGBA', background.size)
        ld = ImageDraw.Draw(light)
        center = int((t / spec.duration * 1.4 - .2) * width)
        ld.polygon([(center - 35, 0), (center + 15, 0), (center + height + 15, height), (center + height - 35, height)], fill=(255, 242, 170, 255))
        from PIL import ImageChops
        light.putalpha(ImageChops.multiply(light.getchannel('A'), layer.getchannel('A')))
        layer = Image.alpha_composite(layer, light)
    if alpha < 1:
        layer.putalpha(layer.getchannel('A').point(lambda a: round(a * alpha)))
    return Image.alpha_composite(background.convert('RGBA'), layer).convert('RGB')


def probe(path: Path) -> dict:
    try:
        return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], stderr=subprocess.PIPE, timeout=30))
    except (subprocess.SubprocessError, OSError) as exc:
        raise ValueError('无法读取视频信息，请确认文件完整且 ffprobe 可用') from exc


def run(args: list[str]) -> None:
    try:
        completed = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', *args], capture_output=True, timeout=900)
    except (subprocess.SubprocessError, OSError) as exc:
        raise ValueError('视频处理超时或 ffmpeg 不可用，输入已保留') from exc
    if completed.returncode:
        raise ValueError('视频处理失败：' + completed.stderr.decode(errors='replace')[-1200:])


def render_video(root: Path, spec: IntroSpec, output: Path, progress: Callable[[float], None], width: int = 1280) -> None:
    ratio = {'16:9': 16 / 9, '9:16': 9 / 16, '1:1': 1}[spec.ratio]
    if spec.ratio == '9:16':
        width = round(width * 9 / 16 / 2) * 2
    height = round(width / ratio / 2) * 2
    if spec.background:
        with Image.open(project_file(root, spec.background)) as source:
            background = ImageOps.fit(ImageOps.exif_transpose(source).convert('RGB'), (width, height))
    else:
        background = Image.new('RGB', (width, height), '#111827')
    original = project_file(root, spec.insert_video) if spec.insert_video else None
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='intro-render-') as scratch:
        intro = Path(scratch) / 'title.mp4'
        # Keep stderr on disk so the pipe cannot block if encoding reports errors.
        with tempfile.TemporaryFile() as log:
            proc = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{width}x{height}', '-r', '24', '-i', 'pipe:0', '-an', '-c:v', 'libx264', '-preset', 'fast', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(intro)], stdin=subprocess.PIPE, stderr=log)
            try:
                for frame in range(round(spec.duration * 24)):
                    proc.stdin.write(render_frame(background, spec, frame / 24).tobytes())
                    if frame % 12 == 0:
                        progress(frame / (spec.duration * 24) * .8)
                proc.stdin.close()
                if proc.wait(timeout=90):
                    log.seek(0)
                    raise ValueError(log.read().decode(errors='replace')[-1200:])
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        if original:
            merged = Path(scratch) / 'merged.mp4'
            graph = f'[0:v]setsar=1,fps=24,setpts=PTS-STARTPTS[a];[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,setpts=PTS-STARTPTS[b];[a][b]concat=n=2:v=1:a=0[v]'
            run(['-i', str(intro), '-i', str(original), '-filter_complex', graph, '-map', '[v]', '-c:v', 'libx264', '-preset', 'fast', '-pix_fmt', 'yuv420p', str(merged)])
            progress(.95)
            # Stream-copy original audio packets, offset by the encoded title duration.
            offset = probe(intro)['format']['duration']
            run(['-i', str(merged), '-itsoffset', str(offset), '-i', str(original), '-map', '0:v', '-map', '1:a?', '-c', 'copy', '-movflags', '+faststart', str(output)])
        else:
            import shutil
            shutil.copyfile(intro, output)
    progress(1.)
