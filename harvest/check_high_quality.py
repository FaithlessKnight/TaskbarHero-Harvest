"""Replay supplied tooltips, failed-run tooltips and page buttons; no game input."""
from engine import Engine
from panels import button, visible
from rules import classify
from vision import asset, header_qualities
from windows_io import Frame, OCR, ROOT


def main():
    engine = Engine.__new__(Engine)
    engine.folder, engine.serial = ROOT / 'runs' / 'high-quality-check', 0
    engine.folder.mkdir(exist_ok=True)
    engine.ocr = OCR()
    try:
        cases = [
            ('星光蓝宝石', asset('ultimate-material-reference'), '至宝'),
            ('天界灵魂石', asset('celestial-soulstone-reference'), '天界'),
            ('运行中的蓝宝石', Frame.load_bmp(ROOT / 'runs' / '20261110-025438-481508' / '00014-inventory-01-local.bmp'), '至宝'),
            ('运行中的灵魂石', Frame.load_bmp(ROOT / 'runs' / '20261110-025438-481508' / '00004-inventory-00-local.bmp'), '天界'),
            ('运行中的永恒护甲', Frame.load_bmp(ROOT / 'runs' / '20261110-025438-481508' / '00106-inventory-17-local.bmp'), '至宝'),
        ]
        for name, frame, quality in cases:
            result, unused = engine.read_saved(frame, 'tooltip', scale=3)
            second, unused = engine.read_saved(frame, 'tooltip-second', scale=2)
            item = classify(0, header_qualities(frame) + result['lines'] + second['lines'])
            if not item.quality:
                item = classify(0, engine.rarity_lines(frame, 'header') + result['lines'] + second['lines'])
            assert (item.action, item.quality) == ('store', quality), (name, item, result)
            print(name + ' -> ' + quality + ' / Ctrl+右键入库', flush=True)
        frame = Frame.load_bmp(ROOT / 'runs' / '20261110-025438-481508' / '00109-warehouse-transfer-unconfirmed.bmp')
        for name in ('cube', 'warehouse', 'inventory'):
            assert visible(frame, name), name
            print(name, '已打开；按钮位置', button(frame, name), flush=True)
    finally:
        engine.ocr.close()


if __name__ == '__main__':
    main()
