"""Offline packaging contracts for the supplied application artwork."""
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / 'apps/desktop'
ASSETS = DESKTOP / 'assets'


def test_platform_icon_containers_and_wiring():
    config = json.loads((DESKTOP / 'package.json').read_text())['build']
    assert config['mac']['icon'] == 'assets/icon.icns'
    assert config['win']['icon'] == 'assets/icon.ico'
    # PE resource editing must remain enabled for executable/shortcut artwork.
    assert config['win']['signAndEditExecutable'] is True
    assert config['nsis']['installerIcon'] == 'assets/icon.ico'
    assert config['nsis']['uninstallerIcon'] == 'assets/icon.ico'
    assert config['linux']['icon'] == 'assets/icon.png'
    icns = (ASSETS / 'icon.icns').read_bytes()
    assert icns[:4] == b'icns'
    assert struct.unpack('>I', icns[4:8])[0] == len(icns)
    ico = (ASSETS / 'icon.ico').read_bytes()
    reserved, kind, count = struct.unpack('<HHH', ico[:6])
    assert (reserved, kind) == (0, 1)
    sizes = []
    for i in range(count):
        width, height, _, _, _, _, size, offset = struct.unpack('<BBBBHHII', ico[6+16*i:22+16*i])
        assert width == height
        sizes.append(width or 256)
        png = ico[offset:offset+size]
        assert len(png) == size and png[:8] == b'\x89PNG\r\n\x1a\n'
        assert struct.unpack('>II', png[16:24]) == (width or 256, height or 256)
    assert sizes == [16, 24, 32, 48, 64, 128, 256]
    png = (ASSETS / 'icon.png').read_bytes()
    assert struct.unpack('>II', png[16:24]) == (1024, 1024)
    assert (DESKTOP / 'public/icon.png').read_bytes() == png
    assert (ROOT / 'web/public/favicon.ico').read_bytes() == ico
    for filename in ('icon.png', 'icon.icns', 'icon.ico'):
        assert (ROOT / 'apps/bootstrap-installer/src-tauri/icons' / filename).read_bytes() == (ASSETS / filename).read_bytes()


def test_supplied_source_preserved():
    source = (ASSETS / 'app-icon-source.png').read_bytes()
    provenance = json.loads((ASSETS / 'app-icon-provenance.json').read_text())
    assert hashlib.sha256(source).hexdigest() == provenance['source_sha256']
    assert struct.unpack('>II', source[16:24]) == (1254, 1254)
    assert 'source_message' not in provenance


def test_macos_icon_has_real_transparent_corners_and_preserves_center_artwork():
    from PIL import Image

    image = Image.open(ASSETS / 'icon-macos.png').convert('RGBA')
    alpha = image.getchannel('A')
    left, top, right, bottom = alpha.getbbox()
    assert 0 < left < right < image.width
    assert 0 < top < bottom < image.height
    assert image.getpixel((left, top))[3] == 0
    assert image.getpixel((image.width // 2, image.height // 2))[3] == 255
    resized = Image.open(ASSETS / 'icon.png').convert('RGBA').resize(
        (right - left, bottom - top), Image.Resampling.LANCZOS
    )
    assert image.getpixel((image.width // 2, image.height // 2)) == resized.getpixel(
        (resized.width // 2, resized.height // 2)
    )
    touch = Image.open(DESKTOP / 'public/apple-touch-icon.png').convert('RGBA')
    assert touch.tobytes() == Image.open(ASSETS / 'icon.png').convert('RGBA').resize(
        touch.size, Image.Resampling.LANCZOS
    ).tobytes()
    icns = Image.open(ASSETS / 'icon.icns')
    icns.size = image.size
    assert icns.convert('RGBA').tobytes() == image.tobytes()
