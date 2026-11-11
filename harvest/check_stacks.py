"""Replay quantity recognition on saved frames without sending game input."""
import json

from check_tooltips import native
from engine import Engine, grid
from windows_io import OCR, ROOT


def main():
    engine = Engine.__new__(Engine)
    engine.config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    engine.folder = ROOT / 'runs' / 'stack-check'
    engine.folder.mkdir(exist_ok=True)
    engine.serial, engine.ocr = 0, OCR()
    try:
        run = ROOT / 'runs' / '20261109-224024-687096'
        before = native(run / '00001-baseline.bmp')
        after = native(run / '00182-cube-00.bmp')
        cases = [
            ('three blue stones with a missing OCR run',
             native(ROOT / 'runs' / '20261110-012556-433654' / '00001-baseline.bmp'), 'inventory', 1, 7, 3, 3),
            ('backpack before selection', before, 'inventory', 1, 7, 3, 2),
            ('backpack after selection', after, 'inventory', 1, 7, 3, 2),
            ('cube reference without a quantity numeral', after, 'cube', 0, 3, 3, 1),
            ('five stacked crafting materials in warehouse', after, 'warehouse', 1, 7, 7, 5),
            ('five stacked tickets in warehouse', after, 'warehouse', 23, 7, 7, 5),
        ]
        for label, frame, key, slot, columns, rows, expected in cases:
            point = grid(engine.config, key, columns, rows)[slot]
            count = engine.read_stack_count(frame, key, point)
            assert count == expected, (label, count, expected)
            print(f'{label}: {count}; passed.', flush=True)
        single = ROOT / 'runs' / '20261109-231910-098317' / '00332-corrode-no-progress.bmp'
        if single.exists():
            from windows_io import Frame
            frame = Frame.load_bmp(single)
            point = grid(engine.config, 'inventory', 7, 3)[1]
            count = engine.read_stack_count(frame, 'inventory', point)
            assert count == 1, count
            print('one silver ingot after corrosion: 1; passed.', flush=True)
    finally:
        engine.ocr.close()


if __name__ == '__main__':
    main()
