"""Replay empty-slot and completed-corrosion evidence without game input."""
from dataclasses import replace
import json
from unittest.mock import Mock

from check_tooltips import native
from engine import Engine, cell_box, grid
from rules import classify
from windows_io import Frame, ROOT


def main():
    config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    run = ROOT / 'runs' / '20261110-010412-117233'
    points = grid(config, 'inventory', 7, 3)
    before = native(run / '00232-baseline.bmp')
    for slot in range(9, 21):
        assert before.crop(cell_box(config, 'inventory', points[slot])).empty_inventory_slot(), slot
    for slot in range(9):
        assert not before.crop(cell_box(config, 'inventory', points[slot])).empty_inventory_slot(), slot
    print('Before hovering: first empty cell and next 11 empties accepted; 9 occupied cells rejected.', flush=True)

    after = Frame.load_bmp(run / '00832-corrode-no-progress.bmp')
    source = after.crop(cell_box(config, 'inventory', points[7]))
    assert source.empty_inventory_slot()
    for slot in range(8, 14):
        assert not after.crop(cell_box(config, 'inventory', points[slot])).empty_inventory_slot(), slot
    print('After corrosion: silver source is empty; adjacent 6 items are not empty.', flush=True)

    engine = Engine.__new__(Engine)
    engine.config, engine.folder, engine.serial = config, ROOT / 'runs' / 'empty-progress-check', 0
    engine.folder.mkdir(exist_ok=True)
    engine.game = Mock()
    engine.game.frame.return_value = after
    engine.neutral, engine.note, engine.baseline = Mock(), Mock(), Mock(return_value=[])
    engine.ocr = Mock()
    engine.ocr.read.side_effect = AssertionError('Empty source must not trigger OCR or hover')
    engine._inventory_pager = Mock()
    engine._inventory_pager.point.return_value = points[7]
    items = [classify(n, ['次元装备', '稀有级', '防御力100']) for n in range(8)]
    items.append(replace(classify(21, ['银锭', '罕见级', '制作材料']),
                         count_location='inventory', view_top=2))
    remaining, consumed = engine.wait_corrode_progress(items, [1] * 9)
    assert remaining == [0] * 9 and consumed == [1] * 9, (remaining, consumed)
    engine.game.hover.assert_not_called()
    engine.game.click.assert_not_called()
    engine.ocr.read.assert_not_called()
    print('Completed batch verified: consumed 9, remaining 0, no re-click, OCR or empty-cell hover.', flush=True)


if __name__ == '__main__':
    main()
