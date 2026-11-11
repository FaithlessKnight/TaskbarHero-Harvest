"""Exercise custom routing with real Windows OCR on a saved tooltip; no game input."""
import json
from pathlib import Path
import tempfile
from unittest.mock import Mock

from check_regressions_140 import pair
from engine import Engine
from supplemental import new_rule
from windows_io import OCR


def main():
    ocr = OCR()
    try:
        with tempfile.TemporaryDirectory(prefix='TaskbarHero-custom-') as folder:
            e = Engine.__new__(Engine)
            e.ocr, e.folder, e.serial = ocr, Path(folder), 0
            before, after, point = pair('rare-scroll')
            rule = new_rule('稀有铭文卷轴', 'material', 'store', ['稀有'])
            e.config = {'bag_columns': 7, 'bag_rows': 3, 'points': {
                'inventory_first': point, 'inventory_last': [point[0] + 240, point[1] + 80]},
                'supplemental_items': [rule]}
            e.baseline_frame = before
            e.game = Mock()
            e.game.frame.return_value = after
            item = e.local_item(0, 'inventory', point)
            info = json.loads(Path(item.image).with_suffix('.json').read_text(encoding='utf-8')) if item else {}
            print(json.dumps({'item': item.to_dict() if item else None, 'custom': info.get('supplemental')},
                             ensure_ascii=False), flush=True)
            assert item and (item.name, item.action, item.custom_rule) == ('稀有铭文卷轴', 'store', rule['id'])
            assert e.action_allowed('store', item)
            e.game.right_click.assert_not_called()
    finally:
        ocr.close()


if __name__ == '__main__':
    main()
