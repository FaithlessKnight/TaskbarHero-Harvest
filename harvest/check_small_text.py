"""Verify header recovery with actual Windows OCR and saved images only."""
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

from check_regressions_140 import pair
from engine import Engine
from tooltip_regions import item_regions
from windows_io import OCR


def main():
    ocr = OCR()
    try:
        with tempfile.TemporaryDirectory(prefix='TaskbarHero-header-') as folder:
            e = Engine.__new__(Engine)
            e.ocr, e.folder, e.serial = ocr, Path(folder), 0
            for name, quality in (('rare-scroll', '稀有'), ('uncommon-scroll', '罕见')):
                before, after, point = pair(name)
                box = item_regions(after, before, point, 40, [329, 164, 32, 32])[0]
                data = e.enhance_header(after.crop(box).padded(), name)
                assert data['qualities'] == [quality] and data['labels'] == ['铭刻材料'] and not data['blocked'], data
                # Independently exercise the Engine fallback when pixel glyph
                # templates supply no type or rarity. Game input stays mocked.
                e.baseline_frame = before
                e.game = Mock()
                e.game.frame.return_value = after
                e.config = {'bag_columns': 7, 'bag_rows': 3, 'points': {
                    'inventory_first': point, 'inventory_last': [585, 260]}}
                with patch('engine.header_qualities', return_value=[]), patch('engine.header_material_types', return_value=[]):
                    item = e.local_item(0, 'inventory', point)
                assert item and (item.action, item.quality, item.material_type) == ('corrode', quality, 'inscription'), item
                print(name, item.action, item.quality, item.material_type, data, flush=True)
    finally:
        ocr.close()


if __name__ == '__main__':
    main()
