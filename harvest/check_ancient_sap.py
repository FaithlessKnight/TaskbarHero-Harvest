"""Real Windows OCR replay of the supplied sap tooltip; never sends game input."""
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
        with tempfile.TemporaryDirectory(prefix='ancient-sap-') as folder:
            for filename in ('ancient-tree-sap-1.48.bmp', 'ancient-tree-sap-local-1.48.bmp'):
                for custom in ('default', 'corrode', 'store'):
                    e = Engine.__new__(Engine)
                    e.ocr, e.folder, e.serial = ocr, Path(folder) / (filename + '-' + custom), 0
                    e.folder.mkdir()
                    sprite = Frame.load_bmp(Path(__file__).parent / 'tests' / 'fixtures' / filename)
                    before = Frame(700, 700, bytes([75, 85, 95, 255]) * 700 * 700)
                    point = [345, 180]
                    rule = new_rule('古树树液', 'material', 'store' if custom == 'store' else 'corrode', ['稀有'])
                    e.config = {'bag_columns': 7, 'bag_rows': 3, 'points': {
                        'inventory_first': point, 'inventory_last': [585, 260]}}
                    if custom != 'default':
                        e.config['supplemental_items'] = [rule]
                    e.baseline_frame = before
                    e.game = Mock()
                    e.game.frame.return_value = place(before, sprite, 100, 100)
                    item = e.local_item(0, 'inventory', point)
                    info = json.loads(Path(item.image).with_suffix('.json').read_text(encoding='utf-8')) if item else {}
                    print(json.dumps({'sample': filename, 'custom': custom,
                        'action': item.action if item else None, 'quality': item.quality if item else None,
                        'kind': item.kind if item else None, 'material_type': item.material_type if item else None,
                        'reason': item.reason if item else None, 'header_enhancement': info.get('header_enhancement')},
                        ensure_ascii=False), flush=True)
                    assert item and (item.action, item.quality, item.kind, item.material_type) == (
                        'store' if custom == 'store' else 'corrode', '稀有', 'material', 'engraving')
                    if custom != 'default':
                        assert item.custom_rule == rule['id']
                    e.game.right_click.assert_not_called()
    finally:
        ocr.close()


if __name__ == '__main__':
    main()
