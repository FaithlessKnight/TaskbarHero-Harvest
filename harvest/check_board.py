"""Replay saved empty/selected cube states without capturing or controlling the game."""
import json
from unittest.mock import Mock

from check_tooltips import native
from engine import Engine
from windows_io import Frame, ROOT


def main():
    engine = Engine.__new__(Engine)
    engine.config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    engine.game = Mock()
    engine.note = Mock()
    run = ROOT / 'runs' / '20261109-234648-835881'
    for name, expected in (('00001-baseline.bmp', []), ('00431-baseline.bmp', [0, 1])):
        frame = native(run / name)
        engine.game.frame.return_value = frame
        actual = engine.occupied('corrode', frame=frame)
        assert actual == expected, (name, actual, expected)
        stable, actual = engine.settled_board('corrode', [0, 1])
        assert actual == expected and stable.pixels == frame.pixels, (name, actual)
        print(name, 'stable occupied slots:', [n + 1 for n in actual], 'passed.', flush=True)
    # The failing 1.12 run never added an item. The hover screenshot has an
    # empty-cell highlight, while the neutral screenshot matches all refs.
    run = ROOT / 'runs' / '20261110-023734-860193'
    neutral = native(run / '00001-baseline.bmp')
    assert engine.occupied('corrode', frame=neutral) == []
    for name in ('00002-inventory-00.bmp', '00085-inventory-14.bmp'):
        hovered = Frame.load_bmp(run / name)
        assert engine.occupied('corrode', frame=hovered) == [0], name
        engine.game.frame.return_value = hovered
        engine.neutral = Mock(side_effect=lambda: setattr(engine.game.frame, 'return_value', neutral))
        engine.neutral()
        unused, actual = engine.settled_board('corrode', list(range(9)))
        assert actual == [], (name, actual)
        print(name, 'hover false positive reproduced; neutral board is empty. Passed.', flush=True)
    engine.game.click.assert_not_called()
    engine.game.right_click.assert_not_called()


if __name__ == '__main__':
    main()
