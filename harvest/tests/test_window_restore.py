"""User-requested settings recovery, close, layout translation and resume."""
import copy
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_displaced_comparison import comparison_pair
from check_regressions_140 import place
from engine import Engine, cell_box, grid
from rules import classify
from window_restore import (header, settings_button, graphics_button, restore_button,
                            close_button, anchors, shifted_config, restore_positions)
from windows_io import Frame, UserStopped, FocusLost
from streaming import StreamRunner
from test_engine import fake_engine
from test_panels import fill_region


class SettingsImageTest(unittest.TestCase):
    def test_actual_supplied_controls_and_correct_restore_row(self):
        frame = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'graphics-settings-restore-1.43.bmp')
        origin = header(frame)
        self.assertEqual(origin, [456, 19])
        self.assertEqual(graphics_button(frame, origin), [448, 78])
        # Similar full rows exist at y=212 (Shift+F11) and 246 (Shift+F12).
        # Only the requested window-position row is allowed.
        self.assertEqual(restore_button(frame, origin), [575, 262])
        # Warehouse and cube also have the same X. Pick settings' X only.
        self.assertEqual(close_button(frame, origin), [614, 32])
        before, after, point = comparison_pair()
        self.assertEqual(settings_button(after), [590, 47])
        self.assertEqual(anchors(after)['inventory'], [342, 359])

    def test_missing_correct_row_never_falls_back_to_similar_restore(self):
        frame = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'graphics-settings-restore-1.43.bmp')
        frame = fill_region(frame, [348, 246, 273, 33], (0, 0, 0))
        self.assertIsNone(restore_button(frame, [456, 19]))

    def test_two_correct_restore_rows_are_ambiguous(self):
        frame = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'graphics-settings-restore-1.43.bmp')
        row = frame.crop([348, 246, 273, 33])
        frame = place(frame, row, 348, 280)
        self.assertIsNone(restore_button(frame, [456, 19]))

    def test_unrelated_close_controls_never_replace_missing_settings_x(self):
        frame = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'graphics-settings-restore-1.43.bmp')
        frame = fill_region(frame, [604, 21, 21, 23], (0, 0, 0))
        self.assertIsNone(close_button(frame, [456, 19]))


def workflow():
    e = fake_engine()
    e.config['points'].update(cube_first=[40, 40], cube_last=[120, 120], neutral=[100, 15],
                             warehouse_first=[200, 60], warehouse_last=[320, 140], inventory_sort=[300, 320])
    e.config.update(warehouse_columns=4, warehouse_rows=3)
    e.config['regions'] = {'mode_label': [250, 15, 20, 10], 'options_corrode': [280, 15, 20, 10]}
    e.current_mode = 'corrode'
    original = {'inventory': [30, 300], 'hero': [80, 10], 'cube': [10, 10], 'warehouse': [200, 10]}
    current = {key: [p[0] + (5 if key == 'cube' else 10), p[1] + 10] for key, p in original.items()}
    config_after = shifted_config(e.config, original, current)
    before = Frame(400, 400, bytes([23, 23, 23, 255]) * 400 * 400)
    for kind, cols, rows in (('inventory', 7, 3), ('cube', 3, 3)):
        for index, point in enumerate(grid(e.config, kind, cols, rows)):
            box = cell_box(e.config, kind, point)
            before = fill_region(before, box, (40 + index * 5, 130, 180))
    after = Frame(400, 400, bytes([23, 23, 23, 255]) * 400 * 400)
    for kind, cols, rows in (('inventory', 7, 3), ('cube', 3, 3)):
        for old, new in zip(grid(e.config, kind, cols, rows), grid(config_after, kind, cols, rows)):
            box = cell_box(e.config, kind, old)
            target = cell_box(config_after, kind, new)
            after = place(after, before.crop(box), *target[:2])
    state = {'phase': 'bag', 'before': before, 'after': after, 'clicks': []}
    settings = Frame(400, 400, bytes([40, 40, 40, 255]) * 400 * 400)
    e.game.frame.side_effect = lambda: before if state['phase'] == 'bag' else state['after'] if state['phase'] == 'after' else settings
    def click(point):
        state['clicks'].append(point)
        expected, next_phase = {'bag': ([330, 35], 'settings'), 'settings': ([130, 80], 'graphics'),
                                'graphics': ([280, 260], 'restored'), 'restored': ([350, 35], 'after')}[state['phase']]
        if point != expected:
            raise AssertionError((point, expected))
        state['phase'] = next_phase
    e.game.click.side_effect = click
    readers = {'header': lambda f: [100, 10] if f is settings else None,
               'anchors': lambda f: current if f is state['after'] else original if f is before else None,
               'settings_button': lambda f: [330, 35] if f is before else None,
               'graphics_button': lambda f, h: [130, 80],
               'restore_button': lambda f, h: [280, 260] if state['phase'] in ('graphics', 'restored') else None,
               'close_button': lambda f, h: [350, 35]}
    return e, state, readers


class RestoreWorkflowTest(unittest.TestCase):
    def test_clicks_all_four_controls_then_preserves_items_and_updates_live_pager(self):
        e, state, readers = workflow()
        source_config = copy.deepcopy(e.config)
        e._inventory_pager = Mock(top=4, row_delta=10)
        with patch.multiple('window_restore', **readers):
            restore_positions(e)
        self.assertEqual(state['clicks'], [[330, 35], [130, 80], [280, 260], [350, 35]])
        self.assertEqual(e.config['points']['inventory_first'], [110, 210])
        self.assertEqual(e.config['points']['cube_first'], [45, 50])
        self.assertEqual(e.config['points']['warehouse_first'], [210, 70])
        self.assertEqual(e._inventory_pager.top, 4)
        self.assertEqual(e._inventory_pager.points[0], [110, 210])
        self.assertEqual(source_config['points']['inventory_first'], [100, 200])
        self.assertNotIn('_window_restore', e.__dict__)
        e.game.right_click.assert_not_called()

    def test_stop_at_each_click_propagates_without_later_inputs(self):
        for stop_at in range(4):
            e, state, readers = workflow()
            original_click = e.game.click.side_effect
            def stopped(point):
                if len(state['clicks']) == stop_at:
                    raise UserStopped('F10')
                original_click(point)
            e.game.click.side_effect = stopped
            with self.subTest(stop_at=stop_at), patch.multiple('window_restore', **readers):
                with self.assertRaises(UserStopped):
                    restore_positions(e)
            self.assertEqual(e.game.click.call_count, stop_at + 1)
            e.game.right_click.assert_not_called()

    def test_resume_after_restore_click_does_not_repeat_it(self):
        e, state, readers = workflow()
        original_click = e.game.click.side_effect
        def interrupted(point):
            original_click(point)
            if point == [280, 260]:
                raise RuntimeError('interrupted after reset was sent')
        e.game.click.side_effect = interrupted
        with patch.multiple('window_restore', **readers):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                restore_positions(e)
            self.assertEqual(e._window_restore['phase'], 'close')
            e.game.click.side_effect = original_click
            restore_positions(e)
        self.assertEqual(state['clicks'].count([280, 260]), 1)
        self.assertEqual(state['clicks'][-1], [350, 35])

    def test_changed_inventory_keeps_recovery_pending_and_does_not_use_old_coordinates(self):
        e, state, readers = workflow()
        state['after'] = fill_region(state['after'], [94, 194, 32, 32], (220, 10, 10))
        with patch.multiple('window_restore', **readers):
            with self.assertRaisesRegex(RuntimeError, '背包或魔方物品变化'):
                restore_positions(e)
        self.assertEqual(e._window_restore['phase'], 'verify')
        self.assertEqual(e.config['points']['inventory_first'], [110, 210])
        e.game.right_click.assert_not_called()

    def test_missing_restore_button_never_clicks_restore_or_close(self):
        e, state, readers = workflow()
        readers['restore_button'] = lambda f, h: None
        with patch.multiple('window_restore', **readers):
            with self.assertRaisesRegex(RuntimeError, '恢复窗口位置一行未确认'):
                restore_positions(e)
        self.assertEqual(state['clicks'], [[330, 35], [130, 80]])

    def test_focus_loss_before_restore_mouse_down_leaves_restore_retry_pending(self):
        e, state, readers = workflow()
        original_click = e.game.click.side_effect
        def interrupted(point):
            if point == [280, 260]:
                error = FocusLost('before reset click')
                error.input_started = False
                raise error
            original_click(point)
        e.game.click.side_effect = interrupted
        with patch.multiple('window_restore', **readers):
            with self.assertRaises(FocusLost):
                restore_positions(e)
            self.assertEqual(e._window_restore['phase'], 'restore')
            e.game.click.side_effect = original_click
            restore_positions(e)
        self.assertEqual(state['clicks'].count([280, 260]), 1)

    def test_runner_finishes_pending_settings_before_returning_partial_batch(self):
        e = fake_engine()
        e.stop = threading.Event()
        e._window_restore = {'phase': 'close'}
        r = StreamRunner(e)
        r.mode, r.phase, r.reset_after_pause = 'corrode', 'fill', True
        r.batch = [classify(0, ['稀有级', '攻击伤害100'])]
        events = []
        def recover(engine):
            events.append('close-settings-and-confirm')
            engine.__dict__.pop('_window_restore')
        e.return_to_inventory = Mock(side_effect=lambda **kwargs: events.append('return-partial-input'))
        r.step = Mock(side_effect=lambda: setattr(r, 'phase', 'done'))
        with patch('window_restore.restore_positions', side_effect=recover):
            r.run()
        self.assertEqual(events, ['close-settings-and-confirm', 'return-partial-input'])
        e.game.click.assert_not_called()


class AutomaticTriggerTest(unittest.TestCase):
    def engine(self):
        e = fake_engine()
        e.game.frame.return_value = Frame(400, 400, bytes([30, 60, 110, 255]) * 400 * 400)
        return e

    def test_distant_tooltip_recovers_then_rehovers_same_slot_at_updated_position(self):
        e = self.engine()
        call = {'n': 0}
        def read(*args):
            call['n'] += 1
            if call['n'] == 1:
                e._last_tooltip_selection = {'method': 'source-icon-unconfirmed', 'candidates': [{'framed_box': [0, 300, 200, 90]}]}
                return None
            e._last_tooltip_selection = {'method': 'nearest'}
            return classify(0, ['普通级', '攻击伤害100'])
        e.local_item = Mock(side_effect=read)
        def reset(engine):
            engine.config['points']['inventory_first'] = [110, 210]
        with patch('window_restore.restore_positions', side_effect=reset) as recover:
            item = e.read_item(0, [])
        self.assertEqual(item.action, 'corrode')
        recover.assert_called_once_with(e)
        self.assertEqual(e.game.hover.call_args.args[0], [110, 210])
        self.assertFalse(e._tooltip_restore_waiting_normal)

    def test_normal_or_missing_tooltip_does_not_open_settings(self):
        for info in ({'method': 'nearest'}, {'method': 'source-icon-unconfirmed', 'candidates': []}):
            e = self.engine()
            def read(*args):
                e._last_tooltip_selection = info
                return classify(0, ['普通级', '攻击伤害100'])
            e.local_item = Mock(side_effect=read)
            with patch('window_restore.restore_positions') as recover:
                e.read_item(0, [])
            recover.assert_not_called()

    def test_persistent_displacement_does_not_loop_settings_for_every_retry(self):
        e = self.engine()
        def read(*args):
            e._last_tooltip_selection = {'method': 'source-icon-unconfirmed', 'candidates': [{'framed_box': [0, 300, 200, 90]}]}
            return None
        e.local_item = Mock(side_effect=read)
        with patch('window_restore.restore_positions') as recover:
            e.read_item(0, [])
            e.read_item(1, [])
        recover.assert_called_once()


if __name__ == '__main__':
    unittest.main()
