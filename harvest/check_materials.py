"""Replay real Windows OCR on four supplied tooltips; no game input."""
from pathlib import Path
import tempfile
from unittest.mock import Mock

from engine import Engine
from windows_io import Frame, OCR


def main():
    ocr = OCR()
    try:
        with tempfile.TemporaryDirectory(prefix='TaskbarHero-materials-') as folder:
            for name in ('decoration-jade', 'decoration-amber', 'decoration-obsidian', 'inscription-scroll'):
                frame = Frame.load_bmp(Path(__file__).parent / 'tests' / 'fixtures' / (name + '.bmp'))
                e = Engine.__new__(Engine)
                e.config = {'bag_columns': 7, 'points': {'inventory_first': [100, 200], 'inventory_last': [340, 280]}}
                e.folder, e.serial, e.ocr = Path(folder) / name, 0, ocr
                e.folder.mkdir()
                e.baseline_frame = Frame(frame.width, frame.height, bytes([0, 0, 0, 255]) * frame.width * frame.height)
                e.game = Mock()
                e.game.frame.return_value = Mock(spec=Frame)
                e.game.frame.return_value.tooltip_boxes.return_value = [[0, 0, frame.width, frame.height]]
                e.game.frame.return_value.crop.return_value = frame
                item = e.local_item(0, 'inventory', [100, 200])
                expected = ('corrode', '稀有', 'inscription') if name == 'inscription-scroll' else ('corrode', '罕见', 'decoration')
                assert item is not None and (item.action, item.quality, item.material_type) == expected, item
                print(name, item.action, item.quality, item.material_type, flush=True)
    finally:
        ocr.close()


if __name__ == '__main__':
    main()
