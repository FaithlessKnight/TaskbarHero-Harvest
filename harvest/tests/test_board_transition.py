from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Engine, cell_box, grid
from rules import classify
from windows_io import Frame
from test_engine import fake_engine, equipment


def setup_engine(folder):
    engine = fake_engine()
    engine.folder, engine.serial = Path(folder), 0
    engine.config['points'].update(cube_first=[12, 12], cube_last=[52, 52])
    engine.board_points = Mock(return_value=grid(engine.config, 'cube', 3, 3))
    empty = frame(engine)
    engine.config['learned']['corrode'] = {
        'empty_board': [empty.crop(cell_box(engine.config, 'cube', p)).template() for p in engine.board_points()]}
    engine.occupied = Engine.occupied.__get__(engine)
    engine.settled_board = Engine.settled_board.__get__(engine)
    clock = {'now': 0}
    engine.game.wait.side_effect = lambda seconds: clock.update(now=clock['now'] + seconds)
    return engine, clock


def frame(engine, occupied=(), color=150):
    width = height = 70
    pixels = bytearray(bytes([20, 20, 20, 255]) * width * height)
    for index in occupied:
        x, y, w, h = cell_box(engine.config, 'cube', engine.board_points()[index])
        for row in range(y, y + h):
            start = (row * width + x) * 4
            pixels[start:start + w * 4] = bytes([color, color // 2, 20, 255]) * w
    return Frame(width, height, bytes(pixels))


class BoardTransitionTest(unittest.TestCase):
    def test_flash_in_empty_slots_is_waited_out_without_a_click(self):
        with tempfile.TemporaryDirectory() as folder:
            engine, clock = setup_engine(folder)
            empty = frame(engine)
            flash = frame(engine, range(9))
            engine.game.frame.side_effect = [flash, flash, empty, empty, empty]
            with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
                actual, occupied = engine.settled_board('corrode', [0, 1])
            self.assertEqual(occupied, [])
            self.assertEqual(actual.pixels, empty.pixels)
            self.assertEqual(engine.game.frame.call_count, 5)
            self.assertEqual(sum(c.args[0] for c in engine.game.wait.call_args_list), 1)
            engine.game.click.assert_not_called()

    def test_occupancy_and_pixels_are_read_from_the_same_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            engine, clock = setup_engine(folder)
            retained = frame(engine, [0])
            engine.game.frame.return_value = retained
            with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
                actual, occupied = engine.settled_board('corrode', [0, 1])
            self.assertEqual(occupied, [0])
            self.assertEqual(actual.pixels, retained.pixels)
            self.assertEqual(engine.game.frame.call_count, 3)

    def test_flickering_slot_list_requires_three_stable_observations(self):
        with tempfile.TemporaryDirectory() as folder:
            engine, clock = setup_engine(folder)
            retained, empty = frame(engine, [0]), frame(engine)
            engine.game.frame.side_effect = [retained, empty, retained, empty, empty, empty]
            with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
                unused, occupied = engine.settled_board('corrode', [0])
            self.assertEqual(occupied, [])
            self.assertEqual(engine.game.frame.call_count, 6)

    def test_persistent_unapproved_slot_stops_and_saves_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            engine, clock = setup_engine(folder)
            engine.game.frame.return_value = frame(engine, [0, 8])
            with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
                with self.assertRaisesRegex(RuntimeError, '持续异常格：9'):
                    engine.settled_board('corrode', [0, 1], timeout=1)
            saved = list(Path(folder).glob('*-cube-unsettled.bmp'))
            self.assertEqual(len(saved), 1)
            evidence = json.loads(saved[0].with_suffix('.json').read_text(encoding='utf-8'))
            self.assertEqual(evidence['allowed'], [0, 1])
            self.assertEqual(evidence['observations'][-1]['unexpected'], [8])
            engine.game.click.assert_not_called()

    def test_a_formerly_consumed_slot_cannot_be_reused_without_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            engine, clock = setup_engine(folder)
            engine.game.frame.return_value = frame(engine, [0, 1])
            with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
                with self.assertRaisesRegex(RuntimeError, '持续异常格：2'):
                    engine.settled_board('corrode', [0], timeout=1)

    def test_stop_request_during_wait_propagates(self):
        with tempfile.TemporaryDirectory() as folder:
            engine, clock = setup_engine(folder)
            engine.game.frame.return_value = frame(engine, range(9))
            engine.game.wait.side_effect = RuntimeError('已停止（F10）')
            with patch('engine.time.monotonic', return_value=0):
                with self.assertRaisesRegex(RuntimeError, 'F10'):
                    engine.settled_board('corrode', [0, 1])
            engine.game.click.assert_not_called()

    def test_success_after_flash_continues_to_sort_and_rescan(self):
        with tempfile.TemporaryDirectory() as folder:
            engine, clock = setup_engine(folder)
            material = replace(classify(11, ['银锭', '稀有级', '制作材料']), count_location='inventory')
            bow = equipment(15, '罕见')
            selected, flash, empty = frame(engine, [0, 1]), frame(engine, range(9)), frame(engine)
            phase = {'clicked': False, 'reads': 0}
            def capture():
                if not phase['clicked']:
                    return selected
                phase['reads'] += 1
                return flash if phase['reads'] <= 2 else empty
            def click(point):
                phase['clicked'] = True
            engine.game.frame.side_effect = capture
            engine.game.click.side_effect = click
            engine.config['max_batches'] = 20
            engine.mode, engine.transfer, engine.verify_board, engine.sort_inventory = Mock(), Mock(), Mock(), Mock()
            engine.scan = Mock(side_effect=[[material, bow], []])
            with patch('engine.sort_button', return_value=[40, 60]), patch(
                    'engine.time.monotonic', side_effect=lambda: clock['now']):
                engine.execute([material, bow])
            engine.game.click.assert_called_once_with([20, 20])
            engine.sort_inventory.assert_called_once()
            self.assertEqual(engine.scan.call_count, 2)


if __name__ == '__main__':
    unittest.main()
