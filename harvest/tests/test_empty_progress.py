from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Engine
from rules import classify
from windows_io import Frame
from test_engine import fake_engine, equipment


def cell(border=True, icon=False):
    width, height = 30, 28
    pixels = bytearray(bytes([23, 23, 23, 255]) * width * height)
    if border:
        for y in range(2):
            for x in range(width):
                n = (y * width + x) * 4
                pixels[n:n + 4] = bytes([30, 70, 120, 255])
        for x in range(7):
            n = x * 4
            pixels[n:n + 4] = bytes([48, 48, 48, 255])
    if icon:
        for y in range(13, 15):
            for x in range(14, 16):
                n = (y * width + x) * 4
                pixels[n:n + 4] = bytes([70, 130, 220, 255])
    return Frame(width, height, bytes(pixels))


def scene(patch):
    width = height = 400
    pixels = bytearray(bytes([23, 23, 23, 255]) * width * height)
    for y in range(patch.height):
        start = ((186 + y) * width + 84) * 4
        pixels[start:start + patch.width * 4] = patch.pixels[y * patch.width * 4:(y + 1) * patch.width * 4]
    return Frame(width, height, bytes(pixels))


class EmptyProgressTest(unittest.TestCase):
    def test_bevel_and_a_few_48_gray_edge_pixels_do_not_make_an_item(self):
        self.assertTrue(cell().empty_inventory_slot())
        self.assertTrue(cell(border=False).empty_inventory_slot())

    def test_even_a_small_colored_icon_in_center_is_not_empty(self):
        self.assertFalse(cell(icon=True).empty_inventory_slot())

    def test_dark_center_with_an_item_sized_colored_surround_is_not_empty(self):
        width, height = 30, 28
        pixels = bytearray(bytes([70, 130, 220, 255]) * width * height)
        for y in range(6, 22):
            for x in range(6, 24):
                start = (y * width + x) * 4
                pixels[start:start + 4] = bytes([23, 23, 23, 255])
        self.assertFalse(Frame(width, height, bytes(pixels)).empty_inventory_slot())

    def test_two_empty_frames_stop_before_hover_local_ocr_or_long_retry(self):
        engine = fake_engine()
        engine.game.frame.return_value = scene(cell())
        engine.local_item, engine.read_frame = Mock(), Mock()
        item = engine.read_item(0, [])
        self.assertTrue(item.empty)
        engine.game.hover.assert_not_called()
        engine.local_item.assert_not_called()
        engine.read_frame.assert_not_called()
        engine.game.wait.assert_called_once_with(0.12)

    def test_item_appearing_in_second_frame_is_read_and_not_skipped(self):
        engine = fake_engine()
        engine.game.frame.side_effect = [scene(cell()), scene(cell(icon=True))]
        engine.local_item = Mock(return_value=equipment(quality='普通'))
        item = engine.read_item(0, [])
        self.assertFalse(item.empty)
        self.assertEqual(item.action, 'corrode')
        engine.game.hover.assert_called_once()

    def test_unknown_type_with_a_real_tooltip_does_not_retry_hover(self):
        engine = fake_engine()
        engine.game.frame.return_value = scene(cell(icon=True))
        engine.local_item = Mock(return_value=classify(0, ['不朽级', '未识别名称']))
        item = engine.read_item(0, [])
        self.assertEqual(item.action, 'keep')
        self.assertFalse(item.empty)
        engine.game.hover.assert_called_once()

    def test_source_last_unit_disappears_with_border_without_ocr(self):
        engine = fake_engine()
        engine.game.frame.return_value = scene(cell())
        engine.local_item, engine.read_frame = Mock(), Mock()
        engine.read_source_count = Engine.read_source_count.__get__(engine)
        expected = replace(classify(0, ['银锭', '罕见级', '制作材料']), count_location='inventory')
        self.assertEqual(engine.read_source_count(expected), 0)
        engine.game.hover.assert_not_called()
        engine.local_item.assert_not_called()
        engine.read_frame.assert_not_called()

    def test_mixed_batch_empty_source_confirms_progress_without_reclick(self):
        engine = fake_engine()
        engine.game.frame.return_value = scene(cell())
        engine.occupied = Mock(return_value=[])
        engine.read_source_count = Engine.read_source_count.__get__(engine)
        material = replace(classify(0, ['银锭', '罕见级', '制作材料']), count_location='inventory')
        with patch('engine.time.monotonic', return_value=0):
            self.assertEqual(engine.wait_corrode_progress([equipment(quality='稀有'), material], [1, 1]),
                             ([0, 0], [1, 1]))
        engine.game.click.assert_not_called()
        engine.game.hover.assert_not_called()

    def test_actual_remaining_gold_ingot_does_not_block_a_completed_mixed_batch(self):
        engine = fake_engine()
        source = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'corrode-source-after.bmp')
        engine.game.frame.return_value = scene(source)
        engine.occupied.return_value = []
        engine.read_source_count = Engine.read_source_count.__get__(engine)
        engine.read_stack_count = Engine.read_stack_count.__get__(engine)
        engine.read_saved = Mock(side_effect=AssertionError('The remaining ingot has no quantity numeral'))
        material = replace(classify(0, ['金锭', '稀有级', '制作材料']), count_location='inventory')
        batch = [material] + [equipment(n, '稀有') for n in range(1, 9)]
        with patch('engine.time.monotonic', side_effect=[0, 0]):
            self.assertEqual(engine.wait_corrode_progress(batch, [1] * 9, fast=True),
                             ([0] * 9, [0] + [1] * 8))
        engine.read_saved.assert_not_called()
        engine.game.click.assert_not_called()
        engine.game.hover.assert_not_called()

    def test_first_actual_empty_stops_entire_scan_without_next_slot(self):
        engine = fake_engine()
        engine.game.frame.return_value = scene(cell())
        engine.local_item = Mock()
        engine._inventory_pager = Mock()
        engine._inventory_pager.columns = 7
        engine._inventory_pager.rows = engine._inventory_pager.total_rows = 3
        engine._inventory_pager.top = 0
        engine._inventory_pager.point.return_value = [100, 200]
        with tempfile.TemporaryDirectory() as folder:
            engine.folder = Path(folder)
            items = engine.scan()
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].empty)
        engine._inventory_pager.advance.assert_not_called()
        engine.game.hover.assert_not_called()
        engine.local_item.assert_not_called()


if __name__ == '__main__':
    unittest.main()
