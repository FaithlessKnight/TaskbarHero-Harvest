"""Replay the supplied obsidian screenshot; no game input."""
from pathlib import Path

from engine import Engine
from rules import classify
from windows_io import Frame, OCR, ROOT


def main():
    image = ROOT / 'assets' / 'obsidian-reference.bmp'
    if not image.exists():
        raise RuntimeError('缺少 assets/obsidian-reference.bmp。')
    engine = Engine.__new__(Engine)
    engine.folder, engine.serial, engine.ocr = ROOT / 'runs' / 'obsidian-check', 0, OCR()
    engine.folder.mkdir(exist_ok=True)
    try:
        frame = Frame.load_bmp(image)
        extra = engine.rarity_lines(frame, 'obsidian')
        item = classify(0, ['黑曜石碎片', *extra, '0 装饰材料'])
        actual = item.action, item.quality, item.kind, item.material_type
        expected = 'corrode', '罕见', 'material', 'decoration'
        assert actual == expected, (actual, extra)
        print('黑曜石碎片：罕见装饰材料，已识别为腐蚀目标。', flush=True)
    finally:
        engine.ocr.close()


if __name__ == '__main__':
    main()
