"""Find the supplied sort icon in saved game screenshots; no game input."""
import json

from check_tooltips import native
from inventory import sort_button
from windows_io import Frame, ROOT


def main():
    config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    config['points'].pop('inventory_sort', None)
    cases = [
        (ROOT / 'runs/20261109-231910-098317/00332-corrode-no-progress.bmp', False),
        (ROOT / 'runs/20261109-224024-687096/00182-cube-00.bmp', True),
    ]
    for path, doubled in cases:
        if not path.exists():
            continue
        frame = native(path) if doubled else Frame.load_bmp(path)
        point = sort_button(frame, config)
        assert point == [422, 597], (path, point)
        print(path.name, 'sort button:', point, 'passed.', flush=True)


if __name__ == '__main__':
    main()
