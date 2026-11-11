"""Counted shortcuts, shared stack consumption and interruption recovery; no input."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rules import classify
from supplemental import new_rule, apply_rules
from windows_io import UserStopped
from test_engine import equipment, fake_engine
from test_streaming import runner
from test_stacks import material


def selected_material(count=5, slot=0):
    return replace(material(), slot=slot, count=1, count_location='inventory_units', source_count=count)


def board_runner(existing=0):
    r = runner()
    r.batch = [equipment(n + 20, '普通') for n in range(existing)]
    board = list(range(existing))
    r.e.occupied.side_effect = lambda mode: list(board)
    r.e.game.right_click.side_effect = lambda *args, **kwargs: board.append(len(board))
    r.e.settled_board.side_effect = lambda *args: (r.e.game.frame(), list(board))
    return r, board


class CountedInputsTest(unittest.TestCase):
    def test_counts_one_to_five_send_exactly_that_many_shortcuts(self):
        for quantity in range(1, 6):
            with self.subTest(quantity=quantity):
                r, board = board_runner()
                r.e.read_source_count.return_value = quantity
                original = material(quantity)
                self.assertFalse(r.accept(original))
                self.assertEqual(r.e.game.right_click.call_count, quantity)
                self.assertTrue(all(c.args == ([100, 200],) and c.kwargs == {'alt': True}
                                    for c in r.e.game.right_click.call_args_list))
                self.assertEqual(len(r.batch), quantity)
                self.assertEqual([i.count for i in r.batch], [1] * quantity)
                self.assertEqual([i.source_count for i in r.batch], [quantity] * quantity)
                self.assertEqual(original.count, quantity)
                self.assertNotIn(original.slot, getattr(r.e, 'vacated_slots', set()))
                r.e.read_item.assert_not_called()
                r.e.game.click.assert_not_called()

    def test_only_free_cube_slots_are_filled_then_callback_stops_scan(self):
        r, board = board_runner(existing=7)
        r.e.read_source_count.return_value = 5
        self.assertTrue(r.accept(material(5)))
        self.assertEqual(r.e.game.right_click.call_count, 2)
        self.assertEqual(len(r.batch), 9)
        self.assertEqual([i.source_count for i in r.batch[-2:]], [5, 5])
        self.assertTrue(any('还需添加 3 个' in c.args[0] for c in r.e.note.call_args_list))

    def test_full_board_never_sends_a_tenth_click(self):
        r, board = board_runner(existing=9)
        r.e.read_source_count.return_value = 5
        self.assertTrue(r.accept(material(5)))
        r.e.game.right_click.assert_not_called()

    def test_quantity_invalid_or_zero_sends_no_input(self):
        for count in (0, 6, True, 2.5):
            with self.subTest(count=count):
                r, board = board_runner()
                r.accept(material(count))
                r.e.game.right_click.assert_not_called()
                self.assertFalse(r.batch)

    def test_unknown_quantity_sends_once_without_any_quantity_recheck(self):
        r, board = board_runner()
        r.e.read_source_count.side_effect = AssertionError('unknown source count is not required')
        r.e.read_stack_count.side_effect = AssertionError('cube count is not required')
        r.accept(material(None))
        r.e.game.right_click.assert_called_once_with([100, 200], alt=True)
        self.assertEqual((r.batch[0].count, r.batch[0].count_location), (1, 'single_unconfirmed'))
        self.assertFalse(r.skipped)
        self.assertNotIn(0, getattr(r.e, 'vacated_slots', set()))

    def test_unknown_quantity_still_rolls_back_a_failed_shortcut(self):
        r, board = board_runner()
        r.e.game.right_click.side_effect = None
        r.accept(material(None))
        r.e.game.right_click.assert_called_once()
        self.assertFalse(r.batch)
        self.assertIn(material(None).identity(), r.skipped)

    def test_unknown_quantity_does_not_bypass_keep_or_lock_decision(self):
        for lines in (['银锭', '已锁定', '稀有级', '制作材料'], ['银锭', '稀有级', '至宝级', '制作材料']):
            r, board = board_runner()
            r.accept(replace(classify(0, lines), count=None))
            r.e.game.right_click.assert_not_called()

    def test_materials_physically_transferred_one_at_a_time_are_counted(self):
        r, board = board_runner()
        r.e.read_source_count.side_effect = [4, 3, 2, 1, 0]
        r.e.read_stack_count.return_value = 1
        r.accept(material(5))
        self.assertEqual(r.e.game.right_click.call_count, 5)
        self.assertEqual([i.count_location for i in r.batch], ['cube'] * 5)
        self.assertEqual(r.e.vacated_slots, {0})

    def test_partial_physical_transfer_does_not_mark_source_as_empty(self):
        r, board = board_runner(existing=8)
        r.e.read_source_count.return_value = 4
        r.e.read_stack_count.return_value = 1
        self.assertTrue(r.accept(material(5)))
        self.assertNotIn(0, getattr(r.e, 'vacated_slots', set()))
        r.e.game.right_click.assert_called_once()

    def test_whole_stack_transfer_stops_before_clicking_the_vacated_slot(self):
        r, board = board_runner()
        r.e.read_source_count.return_value = 0
        r.e.read_stack_count.return_value = 5
        r.accept(material(5))
        r.e.game.right_click.assert_called_once()
        self.assertEqual((r.batch[0].count, r.batch[0].count_location), (5, 'cube'))
        self.assertEqual(r.e.vacated_slots, {0})

    def test_unexpected_second_click_rolls_back_only_current_source_series(self):
        r, board = board_runner(existing=2)
        r.e.read_source_count.return_value = 3
        calls = []
        def click(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                board.append(2)
        r.e.game.right_click.side_effect = click
        r.e.return_to_inventory.side_effect = lambda **kwargs: board.__setitem__(slice(None), [0, 1])
        r.accept(material(3))
        self.assertEqual(r.e.game.right_click.call_count, 2)
        r.e.return_to_inventory.assert_called_once_with(slots={2}, allowed={0, 1, 2, 3})
        self.assertEqual(len(r.batch), 2)
        self.assertIn(material(3).identity(), r.skipped)
        self.assertFalse(r.pending_slots)

    def test_failed_quantity_on_later_click_returns_entire_current_series(self):
        r, board = board_runner(existing=1)
        r.e.read_source_count.side_effect = [3, None]
        r.e.return_to_inventory.side_effect = lambda **kwargs: board.__setitem__(slice(None), [0])
        r.accept(material(3))
        r.e.return_to_inventory.assert_called_once_with(slots={1, 2}, allowed={0, 1, 2})
        self.assertEqual(len(r.batch), 1)
        r.e.game.click.assert_not_called()

    def test_f10_during_second_click_stops_with_pending_slot_for_manual_recovery(self):
        r, board = board_runner()
        r.phase = 'fill'
        r.e.read_source_count.return_value = 5
        def click(*args, **kwargs):
            board.append(len(board))
            if len(board) == 2:
                raise UserStopped('F10')
        r.e.game.right_click.side_effect = click
        with self.assertRaises(UserStopped):
            r.accept(material(5))
        self.assertEqual(len(r.batch), 1)
        self.assertEqual(r.pending_slots, {1})
        r.e.return_to_inventory.assert_not_called()
        r.e.game.click.assert_not_called()

    def test_pause_after_second_shortcut_returns_accepted_and_pending_slots(self):
        r, board = board_runner()
        r.phase = 'fill'
        r.e.read_source_count.return_value = 5
        def settle(*args):
            if len(board) == 2:
                raise RuntimeError('读取中断')
            return r.e.game.frame(), list(board)
        r.e.settled_board.side_effect = settle
        scans = []
        def scan(on_item):
            scans.append(1)
            if len(scans) == 1:
                on_item(material(5))
        r.e.scan = Mock(side_effect=scan)
        r.e.return_to_inventory.side_effect = lambda **kwargs: board.clear()
        r.on_pause = Mock()
        with patch('streaming.sort_button', return_value=[422, 597]):
            r.run()
        r.e.return_to_inventory.assert_called_once_with(allowed={0, 1})
        self.assertEqual(r.e.game.right_click.call_count, 2)
        r.e.game.click.assert_not_called()

    def test_supplemental_corrosion_also_uses_quantity(self):
        r, board = board_runner()
        rule = new_rule('测试宝石', 'material', 'corrode')
        r.e.config['supplemental_items'] = [rule]
        item = apply_rules(classify(0, ['测试宝石', '天界级', '材料']), [rule], ['测试宝石'] * 2)
        item.count = 4
        r.e.read_source_count.return_value = 4
        r.accept(item)
        self.assertEqual(r.e.game.right_click.call_count, 4)
        self.assertTrue(all(i.custom_rule == rule['id'] for i in r.batch))

    def test_warehouse_uses_one_ctrl_shortcut_for_entire_stack(self):
        r, board = board_runner()
        item = replace(classify(0, ['天界级', '材料']), count=5)
        r.e.store_item = Mock(return_value=True)
        r.accept(item)
        r.e.store_item.assert_called_once_with(item, recognized=True)
        r.e.game.right_click.assert_not_called()


class SharedStackProgressTest(unittest.TestCase):
    def test_single_unknown_selection_clear_confirms_completion_without_reading_counts(self):
        e = fake_engine()
        e.occupied.return_value = []
        selected = replace(material(), count_location='single_unconfirmed')
        self.assertEqual(e.wait_corrode_progress([selected], [1], fast=True), ([0], [1]))
        e.read_stack_count.assert_not_called()
        e.read_source_count.assert_not_called()

    def test_retained_unknown_selection_cannot_trigger_a_repeat_commit(self):
        r, board = board_runner()
        r.accept(material(None))
        r.mode, r.phase, r.counts = 'corrode', 'corrode_click', [1]
        r.step()
        with patch('engine.time.monotonic', side_effect=[0, 0, 31]), self.assertRaisesRegex(RuntimeError, '未确认数量减少'):
            r.step()
        r.e.game.click.assert_called_once()
        self.assertEqual(r.phase, 'corrode_result')
        r.e.read_source_count.assert_not_called()

    def test_unknown_count_legacy_transfer_and_verify_allow_one_selection(self):
        e = fake_engine()
        item = material(None)
        e.read_item = Mock(return_value=item)
        e.occupied.return_value = [0]
        batch = [item]
        e.transfer('corrode', batch)
        self.assertEqual(batch[0].count_location, 'single_unconfirmed')
        self.assertEqual(batch[0].count, 1)
        e.game.right_click.assert_called_once()
        e.read_source_count.assert_not_called()
        e.read_item.return_value = material()
        e.verify_board('corrode', batch)
        self.assertEqual(e.read_item.call_args.kwargs, {'read_quantity': False})

    def test_unknown_single_selection_executes_once_then_sorts_and_rescans(self):
        r, board = board_runner()
        r.accept(material(None))
        r.mode, r.phase, r.counts = 'corrode', 'corrode_click', [1]
        r.e.game.click.side_effect = lambda point: board.clear()
        r.step()
        r.step()
        self.assertEqual((r.phase, r.corroded), ('sort', 1))
        r.step()
        r.e.sort_inventory.assert_called_once_with('corrode')
        r.e.game.click.assert_called_once()

    def test_five_references_confirm_five_consumed_with_one_source_read(self):
        e = fake_engine()
        e.occupied.return_value = []
        e.read_source_count.return_value = 0
        batch = [selected_material()] * 5
        self.assertEqual(e.wait_corrode_progress(batch, [1] * 5, fast=True), ([0] * 5, [1] * 5))
        e.read_source_count.assert_called_once_with(batch[0], verify_identity=False)

    def test_capacity_limited_source_keeps_unselected_units_for_next_scan(self):
        e = fake_engine()
        e.occupied.return_value = []
        e.read_source_count.return_value = 3
        batch = [equipment(n + 20, '普通') for n in range(7)] + [selected_material(), selected_material()]
        self.assertEqual(e.wait_corrode_progress(batch, [1] * 9, fast=True), ([0] * 9, [1] * 9))
        e.read_source_count.assert_called_once()

    def test_multiple_sources_are_each_read_and_counted_once(self):
        e = fake_engine()
        e.occupied.return_value = []
        e.read_source_count.side_effect = [0, 0]
        batch = [selected_material(3)] * 3 + [selected_material(2, slot=1)] * 2
        self.assertEqual(e.wait_corrode_progress(batch, [1] * 5, fast=True), ([0] * 5, [1] * 5))
        self.assertEqual(e.read_source_count.call_count, 2)

    def test_partial_consumption_with_retained_slots_updates_shared_baseline_once(self):
        e = fake_engine()
        e.occupied.return_value = [1, 2]
        e.read_source_count.return_value = 2
        batch = [selected_material(3) for _ in range(3)]
        self.assertEqual(e.wait_corrode_progress(batch, [1, 1, 1], fast=True), ([0, 1, 1], [1, 0, 0]))
        self.assertEqual([i.source_count for i in batch], [2] * 3)
        e.occupied.return_value = []
        e.read_source_count.return_value = 0
        self.assertEqual(e.wait_corrode_progress(batch, [0, 1, 1], fast=True), ([0, 0, 0], [0, 1, 1]))

    def test_whole_board_cleared_with_partial_decrement_reports_actual_consumption(self):
        e = fake_engine()
        e.occupied.return_value = []
        e.read_source_count.return_value = 4
        batch = [selected_material() for _ in range(5)]
        remaining, consumed = e.wait_corrode_progress(batch, [1] * 5, fast=True)
        self.assertEqual((remaining, sum(consumed)), ([0] * 5, 1))
        self.assertTrue(any('整理后重新扫描' in c.args[0] for c in e.note.call_args_list))

    def test_greater_decrement_than_added_units_is_not_accepted(self):
        e = fake_engine()
        e.occupied.return_value = []
        e.read_source_count.return_value = 2
        with self.assertRaisesRegex(RuntimeError, '超过本批已添加数量'):
            e.wait_corrode_progress([selected_material()] * 2, [1, 1], fast=True)

    def test_no_progress_or_unchanged_cube_never_authorizes_repeat(self):
        for source, occupied in ((5, []), (5, [0, 1]), (3, [0, 1]), (None, [])):
            with self.subTest(source=source, occupied=occupied):
                e = fake_engine()
                e.occupied.return_value = occupied
                e.read_source_count.return_value = source
                with patch('engine.time.monotonic', side_effect=[0, 0, 31]), self.assertRaisesRegex(RuntimeError, '未确认数量减少'):
                    e.wait_corrode_progress([selected_material()] * 2, [1, 1], fast=True)
                e.game.click.assert_not_called()

    def test_source_read_finishes_before_final_board_confirmation(self):
        e = fake_engine()
        e.occupied.side_effect = [[0, 1], []]
        e.read_source_count.return_value = 0
        trace = Mock()
        trace.attach_mock(e.settled_board, 'board')
        trace.attach_mock(e.read_source_count, 'source')
        e.wait_corrode_progress([selected_material(2)] * 2, [1, 1], fast=True)
        self.assertEqual([c[0] for c in trace.mock_calls], ['board', 'source', 'board'])

    def test_mismatched_group_baselines_cannot_execute_or_be_counted(self):
        e = fake_engine()
        with self.assertRaisesRegex(RuntimeError, '记录不一致'):
            e.wait_corrode_progress([selected_material(5), selected_material(4)], [1, 1], fast=True)
        e.read_source_count.assert_not_called()

    def test_group_board_verification_reads_shared_source_once(self):
        e = fake_engine()
        e.read_item = Mock(return_value=material())
        e.read_source_count.return_value = 5
        batch = [selected_material() for _ in range(3)]
        e.verify_board('corrode', batch)
        e.read_source_count.assert_called_once()
        self.assertTrue(all(c.kwargs == {'read_quantity': False} for c in e.read_item.call_args_list))

    def test_full_counted_batch_clicks_execute_once_then_sorts_before_rescanning(self):
        r, board = board_runner(existing=4)
        r.e.read_source_count.return_value = 5
        self.assertTrue(r.accept(material(5)))
        r.mode, r.phase = 'corrode', 'corrode_click'
        r.counts = [i.count for i in r.batch]
        r.e.game.click.side_effect = lambda point: board.clear()
        r.step()
        self.assertEqual(r.phase, 'corrode_result')
        r.e.read_source_count.return_value = 0
        r.step()
        self.assertEqual((r.phase, r.corroded), ('sort', 1))
        r.e.game.click.assert_called_once()
        r.step()
        r.e.sort_inventory.assert_called_once_with('corrode')


if __name__ == '__main__':
    unittest.main()
