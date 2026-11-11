"""Replay displaced tooltip and comparison from the supplied screenshot only."""
from pathlib import Path
import tempfile
from unittest.mock import Mock

from engine import Engine
from windows_io import Frame, OCR


def comparison_pair():
    after = Frame.load_bmp(Path(__file__).parent / 'tests' / 'fixtures' /
                           'displaced-bow-comparison-1.42.bmp')
    # The user supplied only the after image. Hide both lower panels for a
    # synthetic no-tooltip baseline; preserve the real source icon and Lv.90.
    pixels = bytearray(after.pixels)
    for y in range(449, after.height):
        n = y * after.width * 4
        pixels[n:n + after.width * 4] = bytes([80, 90, 100, 255]) * after.width
    return Frame(after.width, after.height, bytes(pixels)), after, [447, 338]


def main():
    ocr = OCR()
    try:
        with tempfile.TemporaryDirectory(prefix='TaskbarHero-comparison-') as folder:
            e = Engine.__new__(Engine)
            e.ocr, e.folder, e.serial = ocr, Path(folder), 0
            before, after, point = comparison_pair()
            e.baseline_frame = before
            e.game = Mock()
            e.game.frame.return_value = after
            e.config = {'bag_columns': 7, 'bag_rows': 3, 'points': {
                'inventory_first': [367, 258], 'inventory_last': [607, 338]}}
            item = e.local_item(16, 'inventory', point)
            assert item and (item.action, item.quality, item.kind) == ('corrode', '稀有', 'equipment'), item
            assert '超凡' not in item.text and '1269' not in item.text, item
            print(item.to_dict(), flush=True)
    finally:
        ocr.close()


if __name__ == '__main__':
    main()
