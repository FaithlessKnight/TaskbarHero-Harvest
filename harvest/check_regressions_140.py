"""Real Windows OCR with supplied tooltips and synthetic baseline; no game input."""
from pathlib import Path
import tempfile
from unittest.mock import Mock

from engine import Engine
from windows_io import Frame, OCR


FIXTURES = Path(__file__).parent / 'tests' / 'fixtures'
CASES = ('rare-scroll', 'uncommon-scroll', 'uncommon-bow', 'uncommon-orb', 'uncommon-orb2', 'transcendent-bow')


def place(frame, sprite, x, y):
    pixels = bytearray(frame.pixels)
    for row in range(sprite.height):
        n = ((y + row) * frame.width + x) * 4
        pixels[n:n + sprite.width * 4] = sprite.pixels[row * sprite.width * 4:(row + 1) * sprite.width * 4]
    return Frame(frame.width, frame.height, bytes(pixels))


def pair(name):
    sprite = Frame.load_bmp(FIXTURES / (name + '-1.39.bmp'))
    before = Frame(700, 700, bytes([75, 85, 95, 255]) * 700 * 700)
    return before, place(before, sprite, 100, 100), [345, 180]


def displaced_pair():
    after = Frame.load_bmp(FIXTURES / 'displaced-tooltip-1.39.bmp')
    pixels = bytearray(after.pixels)
    for y in range(570, after.height):
        n = (y * after.width + 279) * 4
        pixels[n:n + 226 * 4] = bytes([80, 90, 100, 255]) * 226
    return Frame(after.width, after.height, bytes(pixels)), after, [525, 264]


def replay(e, before, after, point):
    e.baseline_frame = before
    e.config = {'bag_columns': 7, 'bag_rows': 3, 'points': {
        'inventory_first': point, 'inventory_last': [point[0] + 240, point[1] + 80]}}
    e.game = Mock()
    e.game.frame.return_value = after
    return e.local_item(0, 'inventory', point)


def main():
    ocr = OCR()
    try:
        with tempfile.TemporaryDirectory(prefix='TaskbarHero-140-') as folder:
            e = Engine.__new__(Engine)
            e.ocr, e.folder, e.serial = ocr, Path(folder), 0
            for name in CASES:
                item = replay(e, *pair(name))
                expected = ('store', '超凡', 'equipment') if name == 'transcendent-bow' else (
                    'corrode', '稀有' if name == 'rare-scroll' else '罕见',
                    'material' if 'scroll' in name else 'equipment')
                assert item and (item.action, item.quality, item.kind) == expected, (name, item)
                print(name, item.action, item.quality, item.kind, item.material_type, flush=True)
            item = replay(e, *displaced_pair())
            assert item and item.quality == '不朽' and item.material_type == 'crafting', item
            print('displaced-tooltip', item.action, item.quality, item.kind, item.material_type, flush=True)
    finally:
        ocr.close()


if __name__ == '__main__':
    main()
