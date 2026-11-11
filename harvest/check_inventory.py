"""Check empty-slot detection on the saved failing run; no desktop input."""
import json

from check_tooltips import native
from engine import cell_box, grid
from windows_io import ROOT


def main():
    config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    frame = native(ROOT / 'runs' / '20261109-221720-021914' / '00001-baseline.bmp')
    points = grid(config, 'inventory', config['bag_columns'], config['bag_rows'])
    empty = [i + 1 for i, point in enumerate(points)
             if frame.crop(cell_box(config, 'inventory', point)).empty_inventory_slot()]
    assert empty == [21], empty
    print('Saved 21-slot screenshot: first 20 slots occupied; slot 21 empty. Passed.')


if __name__ == '__main__':
    main()
