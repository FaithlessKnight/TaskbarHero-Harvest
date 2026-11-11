"""Real Windows OCR replays of supplied ingots; all game access is mocked."""
import json
from pathlib import Path
import tempfile
from unittest.mock import Mock

from check_regressions_140 import place
from engine import Engine
from supplemental import new_rule
from windows_io import Frame, OCR


def main():
    ocr = OCR()
    try:
        with tempfile.TemporaryDirectory(prefix='ingot-recovery-') as folder:
            for name, title, quality, action in (
                    ('blacksteel-ingot', '黑钢锭', '超凡', 'store'), ('silver-ingot', '银锭', '稀有', 'corrode'),
                    ('iron-ingot', '铁锭', '罕见', 'corrode'), ('blacksteel-local', '黑钢锭', '超凡', 'store'),
                    ('silver-local', '银锭', '稀有', 'corrode')):
                e = Engine.__new__(Engine)
                e.ocr, e.folder, e.serial = ocr, Path(folder), 0
                sprite = Frame.load_bmp(Path(__file__).parent / 'tests' / 'fixtures' / (name + '-1.47.bmp'))
                before = Frame(700, 700, bytes([75, 85, 95, 255]) * 700 * 700)
                after = place(before, sprite, 100, 100)
                point = [345, 180]
                rule = new_rule(title, 'material', action, [quality])
                e.config = {'bag_columns': 7, 'bag_rows': 3, 'points': {
                    'inventory_first': point, 'inventory_last': [585, 260]}, 'supplemental_items': [rule]}
                e.baseline_frame = before
                e.game = Mock()
                e.game.frame.return_value = after
                item = e.local_item(0, 'inventory', point)
                info = json.loads(Path(item.image).with_suffix('.json').read_text(encoding='utf-8')) if item else {}
                print(json.dumps({'sample': name, 'action': item.action if item else None,
                    'quality': item.quality if item else None, 'name': item.name if item else None,
                    'reason': item.reason if item else None, 'title': info.get('supplemental')}, ensure_ascii=False), flush=True)
                assert item and (item.action, item.name, item.quality) == (action, title, quality)
                assert item.custom_rule == rule['id']
                e.game.right_click.assert_not_called()
    finally:
        ocr.close()


if __name__ == '__main__':
    main()
