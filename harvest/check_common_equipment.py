"""Replay real equipment/offering tooltips through local recognition; no game input."""
from unittest.mock import Mock

from engine import Engine
from windows_io import Frame, OCR, ROOT


def main():
    engine = Engine.__new__(Engine)
    engine.config = {'bag_columns': 7, 'points': {'inventory_first': [100, 200], 'inventory_last': [340, 280]}}
    engine.folder, engine.serial = ROOT / 'runs' / 'common-equipment-check', 0
    engine.folder.mkdir(exist_ok=True)
    engine.ocr = OCR()
    fixtures = ROOT / 'tests' / 'fixtures'
    cases = [(ROOT / 'assets' / 'common-bow-reference.bmp', 'corrode', 'equipment', False)]
    cases += [(fixtures / name, 'corrode', 'equipment', True) for name in
              ('common-bow-local.bmp', 'common-arrow-local.bmp', 'common-helmet-local.bmp')]
    cases += [(fixtures / name, 'store', 'material', True) for name in
              ('offering-giant-pumpkin-local.bmp', 'offering-pumpkin-local.bmp')]
    try:
        for source, action, kind, padded in cases:
            frame = Frame.load_bmp(source)
            if padded:
                frame = frame.crop([4, 4, frame.width - 8, frame.height - 8])
            engine.baseline_frame = Frame(frame.width, frame.height,
                                          bytes([40, 40, 40, 255]) * frame.width * frame.height)
            engine.game = Mock()
            after = engine.game.frame.return_value
            after.tooltip_boxes.return_value = [[0, 0, frame.width, frame.height]]
            after.crop.return_value = frame
            item = engine.local_item(0, 'inventory', [100, 200])
            assert item and (item.action, item.kind) == (action, kind), (source, item)
            assert engine.action_allowed(action, item), item
            if action == 'store':
                assert item.material_type == 'offering', item
            else:
                assert item.quality == '普通', item
            engine.game.right_click.assert_not_called()
            engine.game.click.assert_not_called()
            print(source.name + ': ' + (item.quality or '未知品质') + ' / ' + kind + ' / ' + action, flush=True)
    finally:
        engine.ocr.close()


if __name__ == '__main__':
    main()
