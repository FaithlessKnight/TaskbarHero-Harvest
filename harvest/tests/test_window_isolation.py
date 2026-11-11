"""Real failure screenshot plus mocked Windows placement and process ownership."""
import ctypes as C
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from panels import PanelToggleNotFound, toggle_button
from sessions import harvest_all
from window_isolation import GameIsolation, WindowPlacement
from windows_io import Frame, Game, GameClosed, GameTarget, UserStopped
from test_panels import fill_region


class FailureScreenshotTest(unittest.TestCase):
    def test_supplied_frame_has_two_real_game_expand_buttons(self):
        frame = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'two-game-strips.bmp')
        with self.assertRaises(PanelToggleNotFound) as caught:
            toggle_button(frame, 'expand')
        self.assertEqual(caught.exception.count, 2)
        self.assertIn('[833, 803]', str(caught.exception))
        self.assertIn('[395, 804]', str(caught.exception))

    def test_compositor_model_with_either_other_strip_removed_has_one_real_button(self):
        frame = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'two-game-strips.bmp')
        for box, expected in (([42, 725, 366, 125], [833, 803]),
                              ([480, 725, 366, 125], [395, 804])):
            with self.subTest(remaining=expected):
                isolated = fill_region(frame, box, (0, 0, 0))
                self.assertEqual(toggle_button(isolated, 'expand'), expected)


class WindowIsolationTest(unittest.TestCase):
    def setUp(self):
        self.targets = [GameTarget(n * 10, n * 100) for n in range(1, 5)]
        self.states = {t.hwnd: {'pid': t.pid, 'alive': True, 'visible': True,
                               'mode': 1, 'iconic': False} for t in self.targets}
        self.stop, self.log = threading.Event(), Mock()
        self.user = self.enter_patch('window_isolation.user')
        self.enter_patch('window_isolation.window_pid', side_effect=lambda hwnd: self.states[hwnd]['pid'])
        self.enter_patch('window_isolation.check_stop', side_effect=self.check_stop)
        self.enter_patch('sessions.check_stop', side_effect=self.check_stop)
        self.enter_patch('window_isolation.time.sleep')
        self.user.IsWindow.side_effect = lambda hwnd: self.states.get(hwnd, {}).get('alive', False)
        self.user.IsWindowVisible.side_effect = lambda hwnd: self.states[hwnd]['visible']
        self.user.IsIconic.side_effect = lambda hwnd: self.states[hwnd]['iconic']
        self.user.GetWindowPlacement.side_effect = self.get_placement
        self.user.SetWindowPlacement.side_effect = self.set_placement
        self.user.ShowWindow.side_effect = self.show

    def enter_patch(self, name, **kwargs):
        active = patch(name, **kwargs)
        result = active.start()
        self.addCleanup(active.stop)
        return result

    def check_stop(self, stop):
        if stop.is_set():
            raise UserStopped('F10')

    def get_placement(self, hwnd, pointer):
        placement = pointer._obj
        self.assertEqual(placement.length, C.sizeof(WindowPlacement))
        placement.flags, placement.showCmd = 2, self.states[hwnd]['mode']
        placement.rcNormalPosition.left, placement.rcNormalPosition.top = hwnd + 1, hwnd + 2
        placement.rcNormalPosition.right, placement.rcNormalPosition.bottom = hwnd + 971, hwnd + 894
        return True

    def set_placement(self, hwnd, pointer):
        placement = pointer._obj
        self.assertEqual((placement.flags, placement.rcNormalPosition.left,
                          placement.rcNormalPosition.right), (2, hwnd + 1, hwnd + 971))
        self.states[hwnd].update(mode=placement.showCmd, iconic=placement.showCmd in (2, 7))
        return True

    def show(self, hwnd, command):
        if command == 7:
            self.states[hwnd].update(iconic=True, mode=7)
        elif command == 9:
            self.states[hwnd].update(iconic=False, visible=True, mode=1)
        elif command == 0:
            self.states[hwnd]['visible'] = False
        else:
            self.fail('Unexpected window command')
        return True

    def assert_only_current_visible(self, target):
        self.assertFalse(self.states[target.hwnd]['iconic'])
        for other in self.targets:
            if other != target and self.states[other.hwnd]['alive']:
                self.assertTrue(self.states[other.hwnd]['iconic'])

    def test_each_target_is_shown_alone_and_original_normal_maximized_minimized_states_return(self):
        self.states[20]['mode'] = 3
        self.states[30].update(mode=2, iconic=True)
        before = {hwnd: state.copy() for hwnd, state in self.states.items()}
        with GameIsolation(self.targets, self.stop, self.log) as windows:
            for target in self.targets:
                windows.select(target)
                self.assert_only_current_visible(target)
        self.assertEqual(self.states, before)
        self.assertNotIn(0, [call.args[1] for call in self.user.ShowWindow.call_args_list])

    def test_snapshot_failure_changes_no_windows(self):
        self.user.GetWindowPlacement.side_effect = lambda hwnd, p: False if hwnd == 20 else self.get_placement(hwnd, p)
        with self.assertRaisesRegex(RuntimeError, '无法保存 PID 200'):
            with GameIsolation(self.targets, self.stop, self.log):
                self.fail('Snapshot should fail before entering')
        self.user.ShowWindow.assert_not_called()
        self.user.SetWindowPlacement.assert_not_called()

    def test_f10_halfway_through_minimization_restores_every_changed_window(self):
        def show(hwnd, command):
            self.show(hwnd, command)
            self.stop.set()
        self.user.ShowWindow.side_effect = show
        with self.assertRaises(UserStopped):
            with GameIsolation(self.targets, self.stop, self.log) as windows:
                windows.select(self.targets[0])
        self.assertFalse(self.states[20]['iconic'])
        self.user.SetWindowPlacement.assert_called_once()

    def test_failed_minimization_is_bounded_and_does_not_allow_capture(self):
        self.user.ShowWindow.side_effect = lambda *args: False
        with self.assertRaisesRegex(RuntimeError, '最小化未确认'):
            with GameIsolation(self.targets, self.stop, self.log) as windows:
                windows.select(self.targets[0])
                self.fail('Must not continue before other windows are minimized')
        self.assertEqual(self.user.SetWindowPlacement.call_count, 3)

    def test_closed_or_reused_target_is_not_activated(self):
        for alive, pid in ((False, 100), (True, 999)):
            with self.subTest(alive=alive, pid=pid):
                self.states[10].update(alive=True, pid=100)
                with GameIsolation(self.targets, self.stop, self.log) as windows:
                    self.states[10].update(alive=alive, pid=pid)
                    with self.assertRaises(GameClosed):
                        windows.select(self.targets[0])
        self.user.ShowWindow.assert_not_called()

    def test_reused_handle_is_skipped_during_final_restore(self):
        with GameIsolation(self.targets, self.stop, self.log) as windows:
            windows.select(self.targets[0])
            self.states[20]['pid'] = 999
        self.assertNotIn(20, [call.args[0] for call in self.user.SetWindowPlacement.call_args_list])
        self.assertEqual(self.user.SetWindowPlacement.call_count, 2)

    def test_restore_failure_is_reported_and_other_windows_are_still_restored(self):
        self.user.SetWindowPlacement.side_effect = lambda hwnd, p: False if hwnd == 20 else self.set_placement(hwnd, p)
        with self.assertRaises(UserStopped):
            with GameIsolation(self.targets, self.stop, self.log) as windows:
                windows.select(self.targets[0])
                self.stop.set()
                raise UserStopped('F10')
        self.assertEqual(self.user.SetWindowPlacement.call_count, 3)
        self.assertFalse(self.states[30]['iconic'])
        self.assertTrue(any('PID 200 窗口恢复失败' in call.args[0] for call in self.log.call_args_list))

    def test_manual_restore_during_pause_is_minimized_again_by_guard(self):
        with GameIsolation(self.targets, self.stop, self.log) as windows:
            windows.select(self.targets[0])
            count = self.user.ShowWindow.call_count
            windows.ensure(self.targets[0])
            self.assertEqual(self.user.ShowWindow.call_count, count)
            self.states[20].update(mode=1, iconic=False)
            windows.ensure(self.targets[0])
            self.assert_only_current_visible(self.targets[0])
            self.assertEqual(self.user.ShowWindow.call_count, count + 1)

    def test_single_instance_uses_no_window_state_operations(self):
        with GameIsolation(self.targets[:1], self.stop, self.log) as windows:
            windows.select(self.targets[0])
            windows.ensure(self.targets[0])
        self.user.GetWindowPlacement.assert_not_called()
        self.user.ShowWindow.assert_not_called()
        self.user.SetWindowPlacement.assert_not_called()

    def test_real_queue_isolates_before_engine_creation_and_restores_after_engine_close(self):
        trace = []
        def create(config, stop, log, progress, *, target):
            self.assert_only_current_visible(target)
            trace.append(('create', target.pid))
            e = Mock(folder=Path('records') / str(target.pid))
            e.execute_stream.side_effect = lambda pause: e.game._visual_guard()
            e.close.side_effect = lambda: trace.append(('close', target.pid))
            return e
        with patch('sessions.find_games', return_value=self.targets), patch('sessions.Engine', side_effect=create):
            result = harvest_all({}, self.stop, self.log, Mock())
        self.assertEqual(result.completed, 4)
        self.assertEqual(trace, [(event, target.pid) for target in self.targets for event in ('create', 'close')])
        self.assertTrue(all(not state['iconic'] for state in self.states.values()))

    def test_f10_in_real_queue_closes_engine_then_restores_without_starting_next(self):
        e = Mock(folder=Path('records/first'))
        e.execute_stream.side_effect = UserStopped('F10')
        with patch('sessions.find_games', return_value=self.targets), patch('sessions.Engine', return_value=e) as factory:
            with self.assertRaises(UserStopped):
                harvest_all({}, self.stop, self.log, Mock())
        factory.assert_called_once()
        e.close.assert_called_once()
        self.assertTrue(all(not state['iconic'] for state in self.states.values()))


class VisualGuardTest(unittest.TestCase):
    def test_guard_runs_before_screen_capture_and_no_capture_after_failure(self):
        game = Game.__new__(Game)
        game.check = Mock()
        game.rect = Mock(return_value=(0, 0, 970, 892))
        game._visual_guard = Mock(side_effect=RuntimeError('最小化未确认'))
        with patch('windows_io.capture') as capture:
            with self.assertRaisesRegex(RuntimeError, '最小化未确认'):
                game.frame()
        capture.assert_not_called()

    def test_guard_runs_before_pointer_input_and_f10_is_propagated(self):
        game = Game.__new__(Game)
        game.check = Mock()
        game._visual_guard = Mock(side_effect=UserStopped('F10'))
        with patch('windows_io.user') as user:
            with self.assertRaises(UserStopped):
                game.move([50, 60])
        user.SendInput.assert_not_called()


if __name__ == '__main__':
    unittest.main()
