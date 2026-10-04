"""Derive platform-appropriate desktop icons using the repository's declared Pillow dependency.

Run: python3 apps/desktop/scripts/derive-app-icons.py
The immutable supplied PNG is retained; no agent-avatar assets are touched.
"""
from pathlib import Path
import hashlib
import json
import shutil

from PIL import Image, ImageChops, ImageDraw, __version__

DESKTOP = Path(__file__).resolve().parents[1]
ASSETS = DESKTOP / 'assets'
SOURCE = ASSETS / 'app-icon-source.png'


def macos_icon(image):
    """Fit the original artwork into a rounded, inset Dock tile.

    ICNS does not receive iOS-style automatic corner masking. Keep the supplied
    source unchanged and generate real transparency rather than painting corners
    a color that only matches one Dock background.
    """
    size, inset, radius, antialias = 1024, 96, 184, 4
    tile_size = size - 2 * inset
    tile = image.resize((tile_size, tile_size), Image.Resampling.LANCZOS)
    mask = Image.new('L', (tile_size * antialias, tile_size * antialias), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, mask.width - 1, mask.height - 1),
        radius=radius * antialias,
        fill=255,
    )
    mask = mask.resize(tile.size, Image.Resampling.LANCZOS)
    tile.putalpha(ImageChops.multiply(tile.getchannel('A'), mask))
    canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(tile, (inset, inset))
    return canvas


def main():
    source = SOURCE.read_bytes()
    image = Image.open(SOURCE).convert('RGBA')
    # Refuse other geometry rather than crop/stretch the supplied square artwork.
    assert image.size == (1254, 1254)
    image = image.resize((1024, 1024), Image.Resampling.LANCZOS)
    image.save(ASSETS / 'icon.png')
    dock_icon = macos_icon(image)
    dock_icon.save(ASSETS / 'icon-macos.png')
    dock_icon.save(ASSETS / 'icon.icns', format='ICNS')
    sizes = [16, 24, 32, 48, 64, 128, 256]
    image.save(ASSETS / 'icon.ico', format='ICO', sizes=[(n, n) for n in sizes])
    shutil.copyfile(ASSETS / 'icon.png', DESKTOP / 'public/icon.png')
    image.resize((180, 180), Image.Resampling.LANCZOS).save(DESKTOP / 'public/apple-touch-icon.png')
    shutil.copyfile(ASSETS / 'icon.ico', DESKTOP.parents[1] / 'web/public/favicon.ico')
    setup = DESKTOP.parent / 'bootstrap-installer'
    for filename in ('icon.png', 'icon.icns', 'icon.ico'):
        shutil.copyfile(ASSETS / filename, setup / 'src-tauri/icons' / filename)
    shutil.copyfile(ASSETS / 'icon.png', setup / 'public/icon.png')
    (ASSETS / 'app-icon-provenance.json').write_text(json.dumps({

        'source_sha256': hashlib.sha256(source).hexdigest(),
        'source_dimensions': [1254, 1254],
        'transform': 'Pillow LANCZOS proportional resize; entire square source, no crop or redesign',
        'generator': 'apps/desktop/scripts/derive-app-icons.py',
        'pillow_version': __version__,
        'icns': 'PNG-backed ICNS with 128 through 1024 pixel representations; original artwork in a 96px transparent inset with antialiased 184px rounded corners',
        'ico': sizes,
        'web': 'Desktop 180px touch icon and dashboard favicon share the original Eidolon artwork',
    }, indent=2) + '\n')


if __name__ == '__main__':
    main()
