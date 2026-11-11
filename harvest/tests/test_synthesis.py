"""Synthesis batches, backpack return and pause recovery, with simulated input."""
from collections import deque
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rules import batches, classify
from windows_io import UserStopped
from test_engine import fake_engine, equipment
from test_streaming import runner


def same_quality(quality, count, start=0):
    return [classify(start + n, [f'装备{start + n}', quality + '级', '防御力100']) for n in range(count)]


def simulated_inventory(items, fail_slot=None):
    r = runner()
    e = r.e
    state = {'bag': list(items), 'board': {}, 'commits': [], 'returns': [], 'corroded': 0}
    e.inventory_point = Mock(side_effect=lambda slot: [1000 + slot, 2000])
    e.show_inventory_item = Mock()
    e.empty_warehouse = Mock(side_effect=AssertionError('Synthesis does not need warehouse space'))
    e.occupied = Mock(side_effect=lambda mode: sorted(state['board']))
    e.settled_board = Mock(side_effect=lambda mode, allowed: (e.game.frame(), sorted(state['board'])))

    def scan(on_item):
        for slot, item in enumerate(list(state['bag'])):
            if item is not None and on_item(replace(item, slot=slot)):
                break

    def shortcut(point, *, alt=False, ctrl=False):
        if ctrl:
            raise AssertionError('Only the storage helper sends Ctrl')
        if alt:
            slot = point[0] - 1000
            if slot == fail_slot:
                return
            state['board'][len(state['board'])] = state['bag'][slot]
            state['bag'][slot] = None
        else:
            if point != e.board_points()[4] or set(state['board']) != {4}:
                raise AssertionError('Only the completed center output can be collected')
            state['bag'].append(state['board'].pop(4))
            state['returns'].append(point)

    def submit(point):
        if point == e.config['points']['commit_corrode']:
            state['corroded'] += 1
            return
        if point != e.config['points']['commit_synthesize']:
            raise AssertionError('Unexpected button')
        batch = list(state['board'].values())
        if len(batch) != 9 or len({item.quality for item in batch}) != 1:
            raise AssertionError('Synthesis must not mix rarities or submit a partial batch')
        quality = batch[0].quality
        state['commits'].append(quality)
        state['board'] = {4: same_quality('不朽' if quality == '传奇' else '至宝', 1)[0]}

    def sort(mode):
        if state['board']:
            raise AssertionError('Cannot sort before collecting output')
        state['bag'] = [item for item in state['bag'] if item is not None]
        e.vacated_slots = set()

    def store(item, *, recognized=False):
        state['bag'][item.slot] = None
        return True

    def return_inputs(**kwargs):
        state['bag'].extend(state['board'].values())
        state['board'] = {}

    def corrosion(batch, counts, *, fast=False):
        state['board'] = {}
        return [0] * len(counts), list(counts)

    e.scan = Mock(side_effect=scan)
    e.game.right_click.side_effect = shortcut
    e.game.click.side_effect = submit
    e.sort_inventory.side_effect = sort
    e.store_item = Mock(side_effect=store)
    e.return_to_inventory.side_effect = return_inputs
    e.wait_corrode_progress = Mock(side_effect=corrosion)
    return r, state


class SynthesisRulesTest(unittest.TestCase):
    def test_eight_of_each_quality_cannot_be_combined(self):
        self.assertEqual(batches(same_quality('传奇', 8) + same_quality('不朽', 8, 8)), [])

    def test_multiple_batches_keep_quality_separate_and_leave_remainders(self):
        items = same_quality('传奇', 19) + same_quality('不朽', 10, 19)
        plans = batches(items)
        self.assertEqual([(action, len(batch), {i.quality for i in batch}) for action, batch in plans],
                         [('synthesize', 9, {'传奇'}), ('synthesize', 9, {'传奇'}), ('synthesize', 9, {'不朽'})])

    def test_locks_materials_and_selected_corrosion_cannot_join_synthesis(self):
        for quality in ('传奇', '不朽'):
            engine = fake_engine()
            for label in ('制作材料', '装饰材料', '雕刻材料', '供奉材料', '防御力100\n已锁定'):
                item = classify(0, [quality + '级', *label.splitlines()])
                self.assertFalse(engine.action_allowed('synthesize', item))
            item = classify(0, [quality + '级', '防御力100'], selection={'equipment': [quality]})
            engine.config['corrosion_selection'] = {'equipment': [quality]}
            self.assertFalse(engine.action_allowed('synthesize', item))

    def test_mixed_batch_is_rejected_before_any_input(self):
        e = fake_engine()
        batch = same_quality('传奇', 8) + same_quality('不朽', 1, 8)
        for operation in (e.transfer, e.commit):
            with self.assertRaisesRegex(RuntimeError, '同品质'):
                operation('synthesize', batch)
        e.game.click.assert_not_called()
        e.game.right_click.assert_not_called()


class SynthesisLifecycleTest(unittest.TestCase):
    def run_simulation(self, items, max_batches=20, fail_slot=None):
        r, state = simulated_inventory(items, fail_slot)
        r.e.config['max_batches'] = max_batches
        with patch('streaming.sort_button', return_value=[422, 597]):
            r.run()
        r.e.read_item.assert_not_called()
        r.e.verify_board.assert_not_called()
        r.e.game.drag.assert_not_called()
        r.e.empty_warehouse.assert_not_called()
        return r, state

    def test_corrosion_finishes_then_new_immortal_output_can_complete_next_batch(self):
        items = [equipment(0, '普通')] + same_quality('传奇', 9, 1) + same_quality('不朽', 8, 10)
        r, state = self.run_simulation(items)
        self.assertEqual((r.corroded, r.synthesized, r.stored), (1, 2, 1))
        self.assertEqual(state['commits'], ['传奇', '不朽'])
        self.assertEqual(state['returns'], [[14, 10], [14, 10]])
        self.assertEqual(state['board'], {})
        self.assertEqual(state['bag'], [])
        self.assertEqual(sum(bool(call.kwargs.get('alt')) for call in r.e.game.right_click.call_args_list), 19)

    def test_immortal_only_batch_synthesizes_without_legendary(self):
        r, state = self.run_simulation(same_quality('不朽', 9))
        self.assertEqual((r.synthesized, state['commits']), (1, ['不朽']))

    def test_incomplete_groups_leave_everything_in_backpack(self):
        r, state = self.run_simulation(same_quality('传奇', 8) + same_quality('不朽', 8, 8))
        self.assertEqual(len(state['bag']), 16)
        self.assertEqual(r.synthesized, 0)
        r.e.game.right_click.assert_not_called()
        r.e.game.click.assert_not_called()
        r.e.mode.assert_called_once_with('corrode')

    def test_batch_limit_collects_output_before_stopping(self):
        r, state = self.run_simulation(same_quality('传奇', 18), max_batches=1)
        self.assertEqual(r.synthesized, 1)
        self.assertEqual(state['board'], {})
        self.assertEqual(len(state['bag']), 10)
        self.assertEqual(state['returns'], [[14, 10]])

    def test_failed_alt_add_returns_partial_inputs_without_synthesis(self):
        r, state = self.run_simulation(same_quality('传奇', 9), fail_slot=8)
        self.assertEqual((r.synthesized, len(state['bag'])), (0, 9))
        self.assertEqual(state['board'], {})
        self.assertEqual(len(r.skipped), 1)
        r.e.return_to_inventory.assert_called_once_with(allowed=set(range(8)))
        r.e.game.click.assert_not_called()

    def test_synthesis_plan_seeks_each_scanned_item_without_ocr(self):
        r, state = self.run_simulation(same_quality('传奇', 9), max_batches=1)
        self.assertEqual(r.e.show_inventory_item.call_count, 9)
        self.assertEqual([call.args[0].slot for call in r.e.show_inventory_item.call_args_list], list(range(9)))


class SynthesisRecoveryTest(unittest.TestCase):
    def test_noncenter_result_is_not_collected_as_output(self):
        r = runner()
        r.phase, r.mode = 'deposit', 'synthesize'
        r.e.settled_board.return_value = (r.e.game.frame(), [0])
        with self.assertRaisesRegex(RuntimeError, '中间'):
            r.step()
        r.e.game.right_click.assert_not_called()

    def test_result_wait_does_not_accept_single_noncenter_cell(self):
        e = fake_engine()
        e.settled_board = Mock(side_effect=[(e.game.frame(), [0]), (e.game.frame(), [4])])
        self.assertEqual(e.wait_synthesis_output(), 4)
        self.assertEqual(e.settled_board.call_count, 2)
        e.game.right_click.assert_not_called()

    def test_result_pause_never_replays_synthesis_button(self):
        r = runner()
        r.phase, r.mode = 'synthesize_result', 'synthesize'
        r.e.wait_synthesis_output = Mock(side_effect=[RuntimeError('游戏弹窗'), 4])
        r.e.settled_board.side_effect = [(r.e.game.frame(), [4]), (r.e.game.frame(), [])]
        r.e.scan = Mock(return_value=[])
        r.on_pause = Mock()
        r.run()
        self.assertEqual(r.synthesized, 1)
        r.e.game.click.assert_not_called()
        r.e.game.right_click.assert_called_once_with([14, 10])
        r.on_pause.assert_called_once_with('游戏弹窗')

    def test_pause_after_successful_return_does_not_click_empty_center_again(self):
        r = runner()
        r.phase, r.mode = 'deposit', 'synthesize'
        r.e.settled_board.side_effect = [(r.e.game.frame(), [4]), (r.e.game.frame(), [])]
        r.e.confirm_synthesis_return = Mock(side_effect=[RuntimeError('焦点丢失'), None])
        r.e.scan = Mock(return_value=[])
        r.on_pause = Mock()
        r.run()
        self.assertEqual(r.synthesized, 1)
        r.e.game.right_click.assert_called_once_with([14, 10])
        r.e.game.click.assert_not_called()

    def test_failed_return_can_resume_only_for_center_output(self):
        r = runner()
        r.phase, r.mode = 'deposit', 'synthesize'
        r.e.settled_board.side_effect = [(r.e.game.frame(), [4])] * 3 + [(r.e.game.frame(), [])]
        r.e.game.right_click.side_effect = [RuntimeError('游戏弹窗'), None]
        r.e.scan = Mock(return_value=[])
        r.on_pause = Mock()
        r.run()
        self.assertEqual(r.synthesized, 1)
        self.assertEqual(r.e.game.right_click.call_count, 2)
        r.e.game.click.assert_not_called()

    def test_interrupted_plan_returns_known_inputs_then_sorts_before_rescan(self):
        r = runner()
        r.phase, r.mode = 'fill_synthesis', 'synthesize'
        r.batch, r.plan = same_quality('传奇', 1), deque(same_quality('传奇', 8, 1))
        r.e.show_inventory_item = Mock(side_effect=RuntimeError('前台焦点丢失'))
        r.e.scan = Mock(return_value=[])
        r.on_pause = Mock()
        r.run()
        r.e.return_to_inventory.assert_called_once_with(allowed={0})
        r.e.sort_inventory.assert_called_once_with('synthesize')
        r.e.scan.assert_called_once()
        r.e.game.click.assert_not_called()

    def test_f10_while_collecting_stops_without_recovery(self):
        r = runner()
        r.phase, r.mode = 'deposit', 'synthesize'
        r.e.settled_board.return_value = (r.e.game.frame(), [4])
        r.e.game.right_click.side_effect = UserStopped('F10')
        r.on_pause = Mock()
        with self.assertRaises(UserStopped):
            r.run()
        r.on_pause.assert_not_called()
        r.e.sort_inventory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
