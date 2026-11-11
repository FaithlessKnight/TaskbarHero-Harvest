from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Engine
from rules import classify
from windows_io import Frame
from test_engine import fake_engine, equipment


def material(count=1):
    return replace(classify(0, ['星尘锭', '传奇级', '制作材料']), count=count)


def digit_frame():
    pixels = bytearray(bytes([20, 20, 20, 255]) * 30 * 28)
    rows = ['####.', '#..##', '....#', '...##', '..##.', '.##..', '.#...', '#####']
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            if char == '#':
                n = ((17 + y) * 30 + 22 + x) * 4
                pixels[n:n + 4] = bytes([255, 255, 255, 255])
    return Frame(30, 28, bytes(pixels))


class QuantityTest(unittest.TestCase):
    def test_glyph_is_separated_from_the_icon(self):
        glyphs, blank = digit_frame().stack_glyphs()
        self.assertFalse(blank)
        self.assertEqual([(g.width, g.height) for g in glyphs], [(5, 8)])
        repeated = glyphs[0].repeated(5)
        self.assertGreater(repeated.width, 5 * glyphs[0].width)

    def test_count_requires_two_matching_recognitions(self):
        for first, second, expected in [('22222', '22222222', 2), ('22222', '', None),
                                         ('22222', '33333333', None), ('66666', '66666666', None)]:
            with self.subTest(first=first, second=second):
                engine = fake_engine()
                frame = Mock(spec=Frame)
                frame.crop.return_value = digit_frame()
                engine.read_saved = Mock(side_effect=[({'text': first}, 'a.bmp'), ({'text': second}, 'b.bmp'),
                                                     ({'text': ''}, 'c.bmp'), ({'text': ''}, 'd.bmp')])
                self.assertEqual(Engine.read_stack_count(engine, frame, 'inventory', [100, 200]), expected)

    def test_clear_corner_means_one_and_no_ocr_is_required(self):
        engine = fake_engine()
        frame = Mock(spec=Frame)
        frame.crop.return_value = Frame(30, 28, bytes([20, 50, 80, 255]) * 30 * 28)
        engine.read_saved = Mock()
        self.assertEqual(Engine.read_stack_count(engine, frame, 'inventory', [100, 200]), 1)
        engine.read_saved.assert_not_called()

    def test_unconfirmed_count_keeps_corrosion_target_for_single_add(self):
        engine = fake_engine()
        engine.read_stack_count.return_value = None
        engine.read_frame = Mock(return_value=({'lines': [], 'text': ''}, 'full.bmp'))
        engine.local_item = Mock(return_value=material())
        item = engine.read_item(0, [])
        self.assertEqual(item.action, 'corrode')
        self.assertIsNone(item.count)
        self.assertIn('仅 Alt＋右键添加一次', item.reason)

    def test_actual_silver_stack_five_is_confirmed_when_ocr_returns_s(self):
        engine = fake_engine()
        frame = Mock(spec=Frame)
        frame.crop.return_value = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'silver-stack-five.bmp')
        engine.read_saved = Mock(side_effect=[({'text': 'sssss'}, '5.bmp'), ({'text': 'ssssssss'}, '8.bmp')])
        self.assertEqual(Engine.read_stack_count(engine, frame, 'inventory', [100, 200]), 5)
        self.assertEqual(engine.read_saved.call_count, 2)

    def test_five_alias_does_not_override_a_conflicting_quantity(self):
        engine = fake_engine()
        frame = Mock(spec=Frame)
        frame.crop.return_value = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'silver-stack-five.bmp')
        engine.read_saved = Mock(side_effect=[({'text': 'sssss'}, '5.bmp'), ({'text': '33333333'}, '8.bmp')])
        self.assertIsNone(Engine.read_stack_count(engine, frame, 'inventory', [100, 200]))


class StackExecutionTest(unittest.TestCase):
    def test_reference_selection_keeps_backpack_stack_quantity(self):
        engine = fake_engine()
        original = material(5)
        engine.read_item = Mock(return_value=original)
        engine.read_source_count.return_value = 5
        engine.occupied = Mock(return_value=[0])
        batch = [original]
        engine.transfer('corrode', batch)
        self.assertEqual((batch[0].count, batch[0].count_location), (5, 'inventory'))
        engine.read_stack_count.assert_not_called()

    def test_reference_verification_reads_backpack_even_if_cube_icon_has_no_number(self):
        engine = fake_engine()
        selected = replace(material(5), count_location='inventory')
        engine.read_item = Mock(return_value=material(1))
        engine.read_source_count.return_value = 5
        engine.verify_board('corrode', [selected])
        engine.read_source_count.assert_called_once_with(selected)
        self.assertFalse(engine.read_item.call_args.kwargs['read_quantity'])

    def test_reference_count_decrement_with_retained_selection(self):
        engine = fake_engine()
        engine.occupied = Mock(return_value=[0])
        engine.read_source_count.return_value = 4
        with patch('engine.time.monotonic', side_effect=[0, 0]):
            result = engine.wait_corrode_progress([replace(material(5), count_location='inventory')], [5])
        self.assertEqual(result, ([4], [1]))

    def test_selection_clearing_during_backpack_ocr_is_refreshed(self):
        engine = fake_engine()
        engine.occupied = Mock(side_effect=[[0], []])
        engine.read_source_count.return_value = 4
        with patch('engine.time.monotonic', side_effect=[0, 0]):
            result = engine.wait_corrode_progress([replace(material(5), count_location='inventory')], [5])
        self.assertEqual(result, ([0], [1]))

    def test_cleared_selection_only_debits_one_unit_from_a_stack(self):
        engine = fake_engine()
        engine.occupied = Mock(return_value=[])
        engine.read_source_count.return_value = 4
        with patch('engine.time.monotonic', side_effect=[0, 0]):
            result = engine.wait_corrode_progress([replace(material(5), count_location='inventory')], [5])
        self.assertEqual(result, ([0], [1]))

    def test_cleared_selection_without_consumption_does_not_count_as_success(self):
        engine = fake_engine()
        engine.occupied = Mock(return_value=[])
        engine.read_source_count.return_value = 5
        with patch('engine.time.monotonic', side_effect=[0, 0, 31]):
            with self.assertRaisesRegex(RuntimeError, '未确认数量减少'):
                engine.wait_corrode_progress([replace(material(5), count_location='inventory')], [5])

    def test_empty_mixed_batch_finishes_when_one_material_source_is_unchanged(self):
        engine = fake_engine()
        engine.occupied = Mock(return_value=[])
        engine.read_source_count.return_value = 1
        batch = [replace(material(), count_location='inventory')] + [equipment(n, '稀有') for n in range(1, 9)]
        with patch('engine.time.monotonic', side_effect=[0, 0]):
            remaining, consumed = engine.wait_corrode_progress(batch, [1] * 9, fast=True)
        self.assertEqual(remaining, [0] * 9)
        self.assertEqual(consumed, [0] + [1] * 8)
        engine.note.assert_called_once()
        self.assertIn('整理后重新扫描', engine.note.call_args.args[0])
        engine.game.click.assert_not_called()

    def test_empty_mixed_batch_finishes_when_one_source_quantity_is_unreadable(self):
        engine = fake_engine()
        engine.occupied = Mock(return_value=[])
        engine.read_source_count.return_value = None
        batch = [replace(material(3), count_location='inventory'), equipment(1, '稀有')]
        with patch('engine.time.monotonic', side_effect=[0, 0]):
            self.assertEqual(engine.wait_corrode_progress(batch, [3, 1], fast=True), ([0, 0], [0, 1]))
        engine.game.click.assert_not_called()

    def test_partial_board_with_an_unreadable_source_does_not_allow_another_click(self):
        engine = fake_engine()
        engine.occupied = Mock(return_value=[0])
        engine.read_source_count.return_value = None
        batch = [replace(material(3), count_location='inventory'), equipment(1, '稀有')]
        with patch('engine.time.monotonic', side_effect=[0, 0, 31]):
            with self.assertRaisesRegex(RuntimeError, '未确认数量减少'):
                engine.wait_corrode_progress(batch, [3, 1], fast=True)
        engine.game.click.assert_not_called()

    def test_empty_board_with_only_unchanged_materials_still_requires_consumption(self):
        engine = fake_engine()
        engine.occupied = Mock(return_value=[])
        engine.read_source_count.return_value = 1
        batch = [replace(material(), count_location='inventory')] * 2
        with patch('engine.time.monotonic', side_effect=[0, 0, 31]):
            with self.assertRaisesRegex(RuntimeError, '未确认数量减少'):
                engine.wait_corrode_progress(batch, [1, 1], fast=True)
        engine.game.click.assert_not_called()

    def test_batch_reports_actual_consumption_when_cube_clears_early(self):
        engine = fake_engine()
        engine.occupied = Mock(return_value=[0])
        engine.wait_corrode_progress = Mock(return_value=([0], [1]))
        batch = [replace(material(5), count_location='inventory')]
        engine.corrode_batch(batch)
        self.assertEqual(batch[0].count, 1)
        engine.game.click.assert_called_once()

    def test_source_count_checks_identity_before_accepting_remaining_quantity(self):
        engine = fake_engine()
        engine.read_item = Mock(return_value=classify(0, ['别的物品', '稀有级', '材料']))
        self.assertIsNone(Engine.read_source_count(engine, material(3)))
        engine.read_item.return_value = material(2)
        self.assertEqual(Engine.read_source_count(engine, material(3)), 2)

    def test_dragging_one_from_three_updates_input_without_marking_source_empty(self):
        engine = fake_engine()
        original = material(3)
        engine.read_item = Mock(return_value=original)
        engine.read_source_count.return_value = 2
        engine.occupied = Mock(return_value=[0])
        batch = [original]
        engine.transfer('corrode', batch)
        self.assertEqual(batch[0].count, 1)
        self.assertEqual(original.count, 3)
        self.assertEqual(engine.vacated_slots, set())

    def test_whole_stack_arrival_is_counted(self):
        engine = fake_engine()
        original = material(5)
        engine.read_item = Mock(return_value=original)
        engine.read_stack_count.return_value = 5
        engine.occupied = Mock(return_value=[0])
        batch = [original]
        engine.transfer('corrode', batch)
        self.assertEqual(batch[0].count, 5)
        self.assertEqual(engine.vacated_slots, {0})

    def test_more_units_than_approved_cannot_be_consumed(self):
        engine = fake_engine()
        engine.read_item = Mock(return_value=material(5))
        engine.read_stack_count.return_value = 5
        engine.occupied = Mock(return_value=[0])
        with self.assertRaisesRegex(RuntimeError, '超过本次清单'):
            engine.transfer('corrode', [material(2)])
        engine.game.click.assert_not_called()

    def test_five_unit_stack_waits_and_requires_progress_between_clicks(self):
        engine = fake_engine()
        engine.config['action_interval_seconds'] = 3
        engine.verify_board = Mock()
        engine.occupied = Mock(side_effect=[[0, 1], [0, 1]] + [[0]] * 8)
        engine.wait_corrode_progress = Mock(side_effect=[([4, 0], [1, 1]), ([3, 0], [1, 0]),
                                                       ([2, 0], [1, 0]), ([1, 0], [1, 0]), ([0, 0], [1, 0])])
        engine.corrode_batch([material(5), equipment(quality='普通')])
        self.assertEqual(engine.game.click.call_count, 5)
        self.assertEqual([c.args[0] for c in engine.game.wait.call_args_list], [3] * 5)
        self.assertEqual(engine.wait_corrode_progress.call_count, 5)
        self.assertEqual(engine.verify_board.call_args.args[2], [0])
        self.assertEqual(engine.verify_board.call_args.args[1][0].count, 1)

    def test_no_progress_never_sends_second_click(self):
        engine = fake_engine()
        engine.occupied = Mock(return_value=[0])
        engine.wait_corrode_progress = Mock(side_effect=RuntimeError('未确认数量减少'))
        with self.assertRaisesRegex(RuntimeError, '未确认数量减少'):
            engine.corrode_batch([material(5)])
        engine.game.click.assert_called_once()

    def test_actual_count_decrement_is_progress_even_when_slot_stays_occupied(self):
        engine = fake_engine()
        engine.occupied = Mock(return_value=[0])
        engine.read_stack_count.side_effect = [5, 4]
        with patch('engine.time.monotonic', side_effect=[0, 0, 1]):
            self.assertEqual(engine.wait_corrode_progress([material(5)], [5]), ([4], [1]))
        engine.game.wait.assert_called_once_with(0.5)

    def test_disappearing_unit_is_progress(self):
        engine = fake_engine()
        engine.occupied = Mock(return_value=[])
        with patch('engine.time.monotonic', side_effect=[0, 0]):
            self.assertEqual(engine.wait_corrode_progress([material()], [1]), ([0], [1]))

    def test_increasing_quantity_stops(self):
        engine = fake_engine()
        engine.occupied = Mock(return_value=[0])
        engine.read_stack_count.return_value = 5
        with patch('engine.time.monotonic', side_effect=[0, 0]):
            with self.assertRaisesRegex(RuntimeError, '数量变化不符合'):
                engine.wait_corrode_progress([material(3)], [3])

    def test_count_mismatch_blocks_commit(self):
        engine = fake_engine()
        engine.read_item = Mock(return_value=material(2))
        with self.assertRaisesRegex(RuntimeError, '数量复核不一致'):
            engine.verify_board('corrode', [material(3)])
        engine.game.click.assert_not_called()

    def test_partial_stacks_are_rescanned_and_sorted_until_depleted(self):
        engine = fake_engine()
        engine.config['max_batches'] = 20
        engine.mode = Mock()
        engine.sort_inventory = Mock()
        engine.scan = Mock(side_effect=[[material(3)], [material(2)], [material(1)], [], []])
        def transfer(mode, batch):
            batch[0] = replace(batch[0], count=1)
        engine.transfer = Mock(side_effect=transfer)
        engine.commit = Mock()
        with patch('engine.sort_button', return_value=[50, 50]):
            engine.execute([material(3)])
        self.assertEqual(engine.commit.call_count, 3)
        self.assertEqual([c.args[1][0].count for c in engine.commit.call_args_list], [1, 1, 1])
        self.assertEqual(engine.sort_inventory.call_count, 3)


if __name__ == '__main__':
    unittest.main()
