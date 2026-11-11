from dataclasses import replace
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streaming import StreamRunner
from rules import classify
from windows_io import UserStopped, Frame
from test_engine import fake_engine, equipment


def runner():
    e = fake_engine()
    e.stop = threading.Event()
    e.config['max_batches'] = 20
    e.mode = Mock()
    e.sort_inventory = Mock()
    e.return_to_inventory = Mock()
    e.verify_board = Mock(side_effect=AssertionError('No repeated item OCR before submission'))
    e.read_item = Mock(side_effect=AssertionError('Use the already recognized item'))
    e.occupied = Mock(return_value=[])
    e.settled_board = Mock(return_value=(e.game.frame(), [0]))
    return StreamRunner(e)


class AddWhileScanningTest(unittest.TestCase):
    def test_recognized_item_is_added_immediately_without_reading_it_again(self):
        r = runner()
        item = equipment(0, '稀有')
        r.accept(item)
        r.e.game.right_click.assert_called_once_with([100, 200], alt=True)
        r.e.read_item.assert_not_called()
        self.assertEqual(len(r.batch), 1)

    def test_hover_highlight_is_dismissed_before_first_board_check(self):
        r = runner()
        hovered = {'active': True}
        r.e.neutral.side_effect = lambda: hovered.update(active=False)
        r.e.occupied.side_effect = lambda mode: [0] if hovered['active'] else []
        r.accept(equipment(0, '稀有'))
        self.assertEqual(len(r.batch), 1)
        self.assertFalse(r.skipped)
        r.e.return_to_inventory.assert_not_called()

    def test_transient_highlight_is_waited_out_before_adding(self):
        r = runner()
        r.e.occupied.return_value = [0]
        r.e.settled_board.side_effect = [(r.e.game.frame(), []), (r.e.game.frame(), [0])]
        r.accept(equipment(0, '稀有'))
        self.assertEqual(len(r.batch), 1)
        self.assertFalse(r.skipped)
        r.e.return_to_inventory.assert_not_called()

    def test_residual_slot_is_returned_and_current_item_is_skipped(self):
        r = runner()
        r.e.occupied.side_effect = [[0], []]
        r.e.settled_board.return_value = (r.e.game.frame(), [0])
        r.accept(equipment(0, '稀有'))
        r.e.return_to_inventory.assert_called_once_with(slots={0}, allowed={0})
        r.e.game.right_click.assert_not_called()
        self.assertFalse(r.batch)
        self.assertEqual(len(r.skipped), 1)

    def test_residual_slot_is_returned_without_removing_existing_batch(self):
        r = runner()
        r.batch = [equipment(0, '稀有')]
        r.e.occupied.side_effect = [[0, 2], [0]]
        r.e.settled_board.return_value = (r.e.game.frame(), [0, 2])
        r.accept(equipment(1, '罕见'))
        r.e.return_to_inventory.assert_called_once_with(slots={2}, allowed={0, 2})
        self.assertEqual(len(r.batch), 1)
        self.assertEqual(len(r.skipped), 1)

    def test_missing_batch_slot_pauses_without_adding_or_returning_anything(self):
        r = runner()
        r.batch = [equipment(0, '稀有')]
        r.e.settled_board.return_value = (r.e.game.frame(), [])
        with self.assertRaisesRegex(RuntimeError, '槽位缺失'):
            r.accept(equipment(1, '罕见'))
        r.e.game.right_click.assert_not_called()
        r.e.return_to_inventory.assert_not_called()
        r.e.game.click.assert_not_called()

    def test_unchanged_slots_skip_item_and_next_item_uses_same_cube_slot(self):
        r = runner()
        first, second = equipment(0, '稀有'), replace(equipment(1, '稀有'), text='不同装备\n稀有级\n防御力100')
        r.e.settled_board.side_effect = [(r.e.game.frame(), []), (r.e.game.frame(), [0])]
        r.accept(first)
        self.assertIn(first.identity(), r.skipped)
        self.assertFalse(r.batch)
        r.accept(second)
        self.assertEqual([i.slot for i in r.batch], [1])
        r.accept(first)
        self.assertEqual(r.e.game.right_click.call_count, 2)

    def test_unexpected_new_slot_is_returned_then_failed_item_is_skipped(self):
        r = runner()
        r.e.settled_board.return_value = (r.e.game.frame(), [2])
        r.accept(equipment(0, '稀有'))
        r.e.return_to_inventory.assert_called_once_with(slots={2}, allowed={0, 2})
        self.assertFalse(r.batch)
        self.assertEqual(len(r.skipped), 1)

    def test_failed_return_pauses_and_keeps_slot_for_recovery(self):
        r = runner()
        r.e.occupied.return_value = [2]
        r.e.settled_board.return_value = (r.e.game.frame(), [2])
        r.e.return_to_inventory.side_effect = RuntimeError('右键取回未确认')
        with self.assertRaisesRegex(RuntimeError, '右键取回未确认'):
            r.accept(equipment(0, '稀有'))
        self.assertEqual(r.pending_slots, {2})
        r.e.game.click.assert_not_called()

    def test_failed_add_does_not_discard_existing_inputs(self):
        r = runner()
        r.batch = [equipment(0, '稀有')]
        r.e.occupied.return_value = [0]
        r.e.settled_board.return_value = (r.e.game.frame(), [0])
        r.accept(equipment(1, '罕见'))
        self.assertEqual(len(r.batch), 1)
        r.e.game.right_click.assert_called_once()

    def test_material_quantity_selects_one_cube_slot_per_unit_without_tooltip_recheck(self):
        r = runner()
        item = replace(classify(0, ['银锭', '稀有级', '制作材料']), count=3)
        r.e.read_source_count.return_value = 3
        selected = []
        r.e.occupied.side_effect = lambda mode: list(selected)
        r.e.game.right_click.side_effect = lambda *args, **kwargs: selected.append(len(selected))
        r.e.settled_board.side_effect = lambda *args: (r.e.game.frame(), list(selected))
        r.accept(item)
        self.assertEqual([(i.count, i.count_location, i.source_count) for i in r.batch],
                         [(1, 'inventory_units', 3)] * 3)
        self.assertEqual(r.e.game.right_click.call_count, 3)
        self.assertEqual(r.e.read_source_count.call_count, 3)
        r.e.read_item.assert_not_called()

    def test_ninth_item_stops_scan_callback_for_immediate_submission(self):
        r = runner()
        for index in range(9):
            r.e.occupied.return_value = list(range(index))
            r.e.settled_board.return_value = (r.e.game.frame(), list(range(index + 1)))
            self.assertEqual(r.accept(equipment(index, '稀有')), index == 8)
        self.assertEqual(len(r.batch), 9)

    def test_high_quality_uses_current_recognition_for_storage(self):
        r = runner()
        r.e.store_item = Mock()
        item = classify(0, ['宇宙级', '材料'])
        r.accept(item)
        r.e.store_item.assert_called_once_with(item, recognized=True)
        r.e.read_item.assert_not_called()

    def test_ultimate_equipment_and_materials_only_use_ctrl_even_if_all_boxes_selected(self):
        for lines in (['至宝级', '防御力6838'], ['至宝鳅', '装饰材料'], ['天界報', '灵魂石']):
            r = runner()
            item = classify(0, lines, selection={'equipment': ['至宝'], 'material': ['至宝']})
            r.e.config['corrosion_selection'] = {'equipment': ['至宝'], 'material': ['至宝']}
            r.e.store_item = Mock(return_value=True)
            r.accept(item)
            r.e.store_item.assert_called_once_with(item, recognized=True)
            r.e.game.right_click.assert_not_called()
            self.assertFalse(r.batch)

    def test_direct_add_cannot_bypass_storage_priority(self):
        r = runner()
        with self.assertRaisesRegex(RuntimeError, '规则不一致'):
            r.add(classify(0, ['至宝级', '防御力6838']))
        r.e.game.right_click.assert_not_called()

    def test_wrong_target_storage_is_skipped_without_counting_success(self):
        r = runner()
        r.e.store_item = Mock(return_value=False)
        item = classify(0, ['至宝级', '防御力6838'])
        r.accept(item)
        r.accept(item)
        r.e.store_item.assert_called_once_with(item, recognized=True)
        self.assertEqual(r.stored, 0)
        self.assertFalse(r.batch)
        r.e.game.right_click.assert_not_called()


class StreamingStateTest(unittest.TestCase):
    def test_finished_harvest_collapses_pages_before_reporting_done(self):
        r = runner()
        r.e.scan = Mock(return_value=[])
        with patch('streaming.sort_button', return_value=[422, 597]):
            r.run()
        r.e.collapse_panels.assert_called_once()
        self.assertEqual(r.phase, 'done')
        r.e.game.click.assert_not_called()

    def test_collapse_pause_resumes_only_cleanup_without_restarting_harvest(self):
        r = runner()
        r.e.scan = Mock(return_value=[])
        r.e.collapse_panels.side_effect = [RuntimeError('收起未确认'), None]
        r.on_pause = Mock()
        with patch('streaming.sort_button', return_value=[422, 597]):
            r.run()
        r.on_pause.assert_called_once_with('收起未确认')
        r.e.scan.assert_called_once()
        r.e.ensure_panels.assert_called_once()
        self.assertEqual(r.e.collapse_panels.call_count, 2)
        r.e.game.activate.assert_called_once()
        r.e.game.click.assert_not_called()

    def test_batch_limit_still_collapses_pages(self):
        r = runner()
        r.synthesized = r.e.config['max_batches']
        r.choose_synthesis()
        self.assertEqual(r.phase, 'collapse_panels')
        r.step()
        r.e.collapse_panels.assert_called_once()
        self.assertEqual(r.phase, 'done')

    def test_f10_during_cleanup_is_terminal(self):
        r = runner()
        r.phase = 'collapse_panels'
        r.e.collapse_panels.side_effect = UserStopped('F10')
        r.on_pause = Mock()
        with self.assertRaises(UserStopped):
            r.run()
        r.e.collapse_panels.assert_called_once()
        r.on_pause.assert_not_called()
        self.assertEqual(r.phase, 'collapse_panels')

    def test_startup_opens_panels_before_mode_or_scan(self):
        r = runner()
        trace = Mock()
        trace.attach_mock(r.e.ensure_panels, 'panels')
        trace.attach_mock(r.e.mode, 'mode')
        trace.attach_mock(r.e.sort_inventory, 'sort')
        r.step()
        with patch('streaming.sort_button', return_value=[422, 597]):
            r.step()
        self.assertEqual([call[0] for call in trace.mock_calls], ['panels', 'mode', 'sort'])
        self.assertEqual(r.phase, 'fill')

    def test_startup_sort_failure_resumes_before_any_scanning_or_consumption(self):
        r = runner()
        r.e.sort_inventory.side_effect = [RuntimeError('整理尚未完成'), None]
        r.e.scan = Mock(return_value=[])
        r.on_pause = Mock()
        with patch('streaming.sort_button', return_value=[422, 597]):
            r.run()
        r.on_pause.assert_called_once_with('整理尚未完成')
        self.assertEqual(r.e.sort_inventory.call_count, 2)
        r.e.scan.assert_called_once()
        r.e.game.click.assert_not_called()
        r.e.game.right_click.assert_not_called()

    def test_partial_corrosion_batch_commits_without_verifying_item_tooltips(self):
        r = runner()
        r.phase = 'fill'
        item = equipment(0, '稀有')
        r.e.scan = Mock(side_effect=lambda on_item: on_item(item))
        r.step()
        self.assertEqual(r.phase, 'corrode_click')
        r.e.occupied.return_value = [0]
        r.step()
        self.assertEqual(r.phase, 'corrode_result')
        r.e.game.click.assert_called_once_with([20, 20])
        r.e.verify_board.assert_not_called()
        r.e.read_item.assert_not_called()

    def test_result_pause_resumes_result_without_replaying_commit(self):
        r = runner()
        r.phase, r.batch, r.counts = 'corrode_result', [equipment(0, '稀有')], [1]
        r.e.wait_corrode_progress = Mock(side_effect=[RuntimeError('游戏弹窗'), ([0], [1])])
        r.e.scan = Mock(return_value=[])
        r.on_pause = Mock()
        with patch('streaming.sort_button', return_value=[422, 597]):
            r.run()
        r.on_pause.assert_called_once_with('游戏弹窗')
        r.e.wait_corrode_progress.assert_called_with(r.batch or [equipment(0, '稀有')], [1], fast=True)
        r.e.game.click.assert_not_called()
        self.assertEqual(r.corroded, 1)

    def test_completed_mixed_batch_with_remaining_material_sorts_before_rescanning(self):
        r = runner()
        r.phase = 'corrode_result'
        r.batch = [replace(classify(0, ['金锭', '稀有级', '制作材料']), count_location='inventory'),
                   equipment(1, '稀有')]
        r.counts = [1, 1]
        r.e.settled_board.return_value = (r.e.game.frame(), [])
        r.e.read_source_count.return_value = 1
        r.e.scan = Mock(return_value=[])
        r.step()
        self.assertEqual((r.phase, r.corroded, r.counts), ('sort', 1, [0, 0]))
        r.e.scan.assert_not_called()
        r.step()
        r.e.sort_inventory.assert_called_once_with('corrode')
        with patch('streaming.sort_button', return_value=[422, 597]):
            r.step()
        self.assertEqual(r.phase, 'fill')
        r.step()
        r.e.scan.assert_called_once()
        r.e.game.click.assert_not_called()

    def test_interrupted_fill_returns_partial_inputs_and_rescans_after_confirmation(self):
        r = runner()
        r.phase, r.batch = 'fill', [equipment(0, '稀有')]
        r.e.scan = Mock(side_effect=[RuntimeError('前台焦点丢失'), []])
        r.on_pause = Mock()
        with patch('streaming.sort_button', return_value=[422, 597]):
            r.run()
        r.e.return_to_inventory.assert_called_once_with(allowed={0})
        r.on_pause.assert_called_once()
        r.e.game.click.assert_not_called()

    def test_interruption_after_shortcut_can_return_unrecorded_selection(self):
        r = runner()
        r.phase = 'fill'
        item = equipment(0, '稀有')
        r.e.settled_board.side_effect = RuntimeError('前台焦点丢失')
        r.e.scan = Mock()
        scanned = {'count': 0}
        def scan(on_item):
            scanned['count'] += 1
            if scanned['count'] == 1:
                on_item(item)
        r.e.scan.side_effect = scan
        r.on_pause = Mock()
        with patch('streaming.sort_button', return_value=[422, 597]):
            r.run()
        r.e.game.right_click.assert_called_once_with([100, 200], alt=True)
        r.e.return_to_inventory.assert_called_once_with(allowed={0})
        r.e.game.click.assert_not_called()
        r.on_pause.assert_called_once()
        self.assertFalse(r.pending_slots)

    def test_f10_after_shortcut_never_starts_automatic_recovery(self):
        r = runner()
        r.phase = 'fill'
        r.e.scan = Mock(side_effect=lambda on_item: on_item(equipment(0, '稀有')))
        r.e.settled_board.side_effect = UserStopped('F10')
        r.on_pause = Mock()
        with self.assertRaises(UserStopped):
            r.run()
        r.on_pause.assert_not_called()
        r.e.return_to_inventory.assert_not_called()

    def test_f10_is_terminal_and_does_not_show_resume_dialog(self):
        r = runner()
        r.e.mode.side_effect = UserStopped('F10')
        r.on_pause = Mock()
        with self.assertRaises(UserStopped):
            r.run()
        r.on_pause.assert_not_called()

    def test_user_stop_while_paused_does_not_resume(self):
        r = runner()
        r.e.mode.side_effect = RuntimeError('游戏弹窗')
        r.on_pause = Mock(side_effect=lambda message: r.e.stop.set())
        with self.assertRaises(UserStopped):
            r.run()
        r.e.game.activate.assert_not_called()

    def test_synthesis_uses_nine_same_quality_even_with_different_levels(self):
        r = runner()
        r.phase = 'fill'
        items = [equipment(i) for i in range(8)] + [replace(equipment(8), level=80)]
        r.e.scan = Mock(side_effect=lambda on_item: [on_item(item) for item in items])
        r.step()
        self.assertEqual((r.mode, r.phase), ('synthesize', 'prepare'))
        self.assertEqual(len(r.plan), 9)
        self.assertEqual({i.level for i in r.plan}, {80, 90})
        r.e.game.right_click.assert_not_called()

    def test_incomplete_synthesis_returns_inputs_without_submitting(self):
        r = runner()
        r.mode, r.phase = 'synthesize', 'fill_synthesis'
        r.e.scan = Mock(return_value=[])
        r.batch = [equipment(i) for i in range(8)]
        r.step()
        self.assertEqual(r.phase, 'sort_partial')
        r.e.return_to_inventory.assert_called_once()
        r.e.game.click.assert_not_called()

    def test_sort_retry_does_not_replay_corrosion(self):
        r = runner()
        r.phase = 'sort'
        r.e.sort_inventory.side_effect = [RuntimeError('整理暂未完成'), None]
        r.e.scan = Mock(return_value=[])
        r.on_pause = Mock()
        with patch('streaming.sort_button', return_value=[422, 597]):
            r.run()
        r.e.game.click.assert_not_called()
        self.assertEqual(r.e.sort_inventory.call_count, 2)


if __name__ == '__main__':
    unittest.main()
