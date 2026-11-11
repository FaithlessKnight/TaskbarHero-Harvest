"""Replay the v1.8 error and material/required-level screenshots; no game input."""
import json
from unittest.mock import Mock

from engine import Engine, grid
from windows_io import Frame, OCR, ROOT
from check_tooltips import native


def main():
    engine = Engine.__new__(Engine)
    engine.config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    engine.config['corrosion_selection'] = {'equipment': ['普通', '罕见', '稀有'],
                                          'material': ['普通', '罕见', '稀有', '传奇', '不朽']}
    engine.folder = ROOT / 'runs' / 'recognition-check'
    engine.folder.mkdir(exist_ok=True)
    engine.serial, engine.ocr = 0, OCR()
    run = ROOT / 'runs' / '20261110-001635-609655'
    cases = [
        ('00002-inventory-00.bmp', 'inventory', 0, ('corrode', '不朽', 'material', None)),
        ('00012-inventory-01.bmp', 'inventory', 1, ('corrode', '不朽', 'material', None)),
        ('00022-inventory-02.bmp', 'inventory', 2, ('corrode', '不朽', 'material', None)),
        ('00032-inventory-03.bmp', 'inventory', 3, ('corrode', '不朽', 'material', None)),
        ('00042-inventory-04.bmp', 'inventory', 4, ('corrode', '不朽', 'material', None)),
        ('00052-inventory-05.bmp', 'inventory', 5, ('keep', '', '', None)),
        ('00062-inventory-07.bmp', 'inventory', 7, ('corrode', '传奇', 'material', None)),
        ('00093-inventory-11.bmp', 'inventory', 11, ('synthesize', '传奇', 'equipment', 80)),
        ('00296-cube-05-retry.bmp', 'cube', 5, ('corrode', '稀有', 'equipment', None)),
    ]
    try:
        for name, key, slot, expected in cases:
            # Use the paired baseline for cube verification; inventory previews
            # share the first baseline while the viewport remains unchanged.
            if key == 'cube':
                candidates = sorted(run.glob('*-baseline.bmp'))
                baseline = max(p for p in candidates if p.name < name)
            else:
                baseline = run / '00001-baseline.bmp'
            engine.baseline_frame = native(baseline)
            engine.game = Mock()
            engine.game.frame.return_value = native(run / name)
            point = grid(engine.config, key, 3 if key == 'cube' else 7, 3)[slot]
            item = engine.local_item(slot, key, point)
            actual = (item.action, item.quality, item.kind, item.level) if item else None
            print(name, actual, 'expected', expected, flush=True)
            assert actual == expected, (name, item, expected)
            engine.game.right_click.assert_not_called()
            engine.game.drag.assert_not_called()
    finally:
        engine.ocr.close()


if __name__ == '__main__':
    main()
