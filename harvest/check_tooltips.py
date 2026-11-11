"""Replay saved screenshots without sending mouse input or capturing the desktop."""
import argparse
import json
import re
from unittest.mock import Mock

from engine import Engine
from windows_io import Frame, OCR, ROOT


def native(path):
    frame = Frame.load_bmp(path)
    return Frame(frame.width // 2, frame.height // 2, b''.join(
        frame.pixels[(y * frame.width + x) * 4:(y * frame.width + x) * 4 + 4]
        for y in range(0, frame.height, 2) for x in range(0, frame.width, 2)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--page', action='store_true', help='Also inspect the saved backpack page')
    args = parser.parse_args()
    config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    folder = ROOT / 'runs' / 'tooltip-check'
    folder.mkdir(exist_ok=True)
    cases = [
        ('20261109-215710-902160', '00155-baseline.bmp', '00156-cube-00.bmp', 'cube', 0,
         ('corrode', '稀有', 'equipment')),
        ('20261109-215710-902160', '00145-baseline.bmp', '00146-inventory-03.bmp', 'inventory', 3,
         ('corrode', '稀有', 'equipment')),
        ('20261109-213929-372647', '00001-baseline.bmp', '00003-inventory-00-retry.bmp', 'inventory', 0,
         ('corrode', '传奇', 'material')),
        ('20261109-215710-902160', '00001-baseline.bmp', '00012-inventory-02.bmp', 'inventory', 2,
         ('store', '稀有', 'material')),
        ('20261109-215710-902160', '00001-baseline.bmp', '00022-inventory-04.bmp', 'inventory', 4,
         ('store', '罕见', 'material')),
        ('20261109-215710-902160', '00001-baseline.bmp', '00053-inventory-11.bmp', 'inventory', 11,
         ('synthesize', '传奇', 'equipment')),
    ]
    if args.page:
        run = '20261109-215710-902160'
        seen = set()
        for path in sorted((ROOT / 'runs' / run).glob('*-inventory-??.bmp')):
            slot = int(re.search(r'inventory-(\d+)', path.name)[1])
            if slot not in seen:
                cases.append((run, '00001-baseline.bmp', path.name, 'inventory', slot, None))
                seen.add(slot)
    engine = Engine.__new__(Engine)
    engine.config, engine.folder, engine.serial = config, folder, 0
    engine.ocr = OCR()
    try:
        for run, before, after, key, slot, expected in cases:
            engine.baseline_frame = native(ROOT / 'runs' / run / before)
            frame = native(ROOT / 'runs' / run / after)
            engine.game = Mock()
            engine.game.frame.return_value = frame
            from engine import grid
            point = grid(config, key, 3 if key == 'cube' else config['bag_columns'],
                         3 if key == 'cube' else config['bag_rows'])[slot]
            step = (config['points'][key + '_last'][0] - config['points'][key + '_first'][0]) / (
                2 if key == 'cube' else config['bag_columns'] - 1)
            boxes = frame.tooltip_boxes(engine.baseline_frame, point, step)
            print(after, frame.width, frame.height, point, boxes, flush=True)
            for n, box in enumerate(boxes):
                frame.crop(box).save(folder / f'{key}-{slot}-{n}.bmp')
            item = engine.local_item(slot, key, point)
            print(json.dumps(item.to_dict() if item else None, ensure_ascii=False), flush=True)
            if expected is not None:
                assert item and (item.action, item.quality, item.kind) == expected, (after, item, expected)
                if slot in (2, 4):
                    assert '供奉' in item.reason, item
    finally:
        engine.ocr.close()


if __name__ == '__main__':
    main()
