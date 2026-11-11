"""Focus loss recovery with simulated Windows APIs and submitted operations."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from windows_io import Game, GameClosed, FocusLost, FocusRecoveryFailed, UserStopped
from test_engine import equipment
from test_streaming import runner


class FocusWindowTest(unittest.TestCase):
    def setUp(self):
        self.g = Game.__new__(Game)
        self.g.hwnd, self.g.pid = 123, 100
        self.g.stop = threading.Event()
        self.g.original_size = (500, 500)
        self.g.expected_cursor = None
        self.g.rect = Mock(return_value=(100, 200, 500, 500))
        self.g.wait = Mock()
        self.g._focus_log = Mock()
        user_patch = patch('windows_io.user')
        pid_patch = patch('windows_io.window_pid', side_effect=lambda hwnd: 100 if hwnd == 123 else 999)
        self.user = user_patch.start()
        self.pid = pid_patch.start()
        self.addCleanup(user_patch.stop)
        self.addCleanup(pid_patch.stop)
        self.user.IsWindow.return_value = True
        self.user.IsIconic.return_value = False
        self.user.GetForegroundWindow.return_value = 999
        self.user.GetAsyncKeyState.return_value = 0
        self.user.SetForegroundWindow.side_effect = lambda hwnd: setattr(self.user.GetForegroundWindow, 'return_value', hwnd)
        self.user.SendInput.return_value = 1

    def test_lost_focus_restores_bound_game_before_reading_frame(self):
        with patch('windows_io.capture', return_value='frame') as capture:
            self.assertEqual(self.g.frame(), 'frame')
        self.user.SetForegroundWindow.assert_called_once_with(123)
        capture.assert_called_once_with((100, 200, 500, 500), 1)
        self.user.SendInput.assert_not_called()
        self.assertIn('已恢复', self.g._focus_log.call_args.args[0])
        self.assertIn('当前前台 PID 999', self.g._focus_log.call_args_list[0].args[0])

    def test_minimized_game_is_restored_before_activation_without_clicking(self):
        self.user.IsIconic.return_value = True
        self.user.ShowWindow.side_effect = lambda *args: setattr(self.user.IsIconic, 'return_value', False)
        with patch('windows_io.api', return_value=self.user.ShowWindow):
            self.g.ready()
        self.user.ShowWindow.assert_called_once_with(123, 9)
        self.user.SetForegroundWindow.assert_called_once_with(123)
        self.user.SendInput.assert_not_called()

    def test_already_focused_game_does_not_activate_or_wait(self):
        self.user.GetForegroundWindow.return_value = 123
        self.g.ready()
        self.user.SetForegroundWindow.assert_not_called()
        self.g.wait.assert_not_called()

    def test_activation_refused_three_times_pauses_without_capturing_or_input(self):
        self.user.SetForegroundWindow.side_effect = None
        with patch('windows_io.capture') as capture:
            with self.assertRaises(FocusRecoveryFailed):
                self.g.frame()
        self.assertEqual(self.user.SetForegroundWindow.call_count, 3)
        capture.assert_not_called()
        self.user.SendInput.assert_not_called()

    def test_repeated_focus_stealing_shares_a_budget_across_operations(self):
        with patch('windows_io.time.monotonic', return_value=100):
            for unused in range(3):
                self.user.GetForegroundWindow.return_value = 999
                self.g.ready()
            self.user.GetForegroundWindow.return_value = 999
            with self.assertRaises(FocusRecoveryFailed):
                self.g.ready()
        self.assertEqual(self.user.SetForegroundWindow.call_count, 3)
        with patch('windows_io.time.monotonic', return_value=116):
            self.g.ready()
        self.assertEqual(self.user.SetForegroundWindow.call_count, 4)

    def test_f10_during_activation_prevents_second_attempt_or_screenshot(self):
        self.user.SetForegroundWindow.side_effect = lambda hwnd: self.g.stop.set()
        with patch('windows_io.capture') as capture:
            with self.assertRaises(UserStopped):
                self.g.frame()
        self.user.SetForegroundWindow.assert_called_once_with(123)
        self.user.SendInput.assert_not_called()
        capture.assert_not_called()

    def test_pid_change_during_activation_is_terminal(self):
        self.user.SetForegroundWindow.side_effect = lambda hwnd: setattr(self.pid, 'side_effect', lambda hwnd: 200)
        with self.assertRaises(GameClosed):
            self.g.ready()
        self.user.SetForegroundWindow.assert_called_once_with(123)
        self.user.SendInput.assert_not_called()

    def test_dimensions_changed_after_restore_still_block_input(self):
        self.g.rect.return_value = (100, 200, 600, 500)
        with self.assertRaisesRegex(RuntimeError, '尺寸发生变化'):
            self.g.ready()
        self.user.SendInput.assert_not_called()

    def test_no_activation_with_an_unreleased_button_or_modifier(self):
        for held in ({'left'}, {'right'}, {'key-18'}):
            self.g._held_inputs = held
            with self.assertRaises(FocusLost):
                self.g.ready()
        self.user.SetForegroundWindow.assert_not_called()

    def test_manual_cursor_movement_remains_a_separate_error(self):
        self.user.GetForegroundWindow.return_value = 123
        self.g.expected_cursor = (10, 20)
        self.user.GetCursorPos.side_effect = lambda pointer: setattr(pointer._obj, 'x', 100)
        with self.assertRaisesRegex(RuntimeError, '手动移动鼠标'):
            self.g.ready()
        self.user.SetForegroundWindow.assert_not_called()

    def test_click_failure_before_down_is_marked_unsent(self):
        self.g.move = Mock()
        self.g.require_target = Mock()
        with self.assertRaises(FocusLost) as caught:
            self.g.click([10, 20])
        self.assertEqual(caught.exception.input_action, 'left_click')
        self.assertIs(caught.exception.input_started, False)
        self.user.SendInput.assert_not_called()

    def test_focus_loss_after_down_releases_button_and_marks_sent(self):
        self.user.GetForegroundWindow.return_value = 123
        self.g.move, self.g.require_target = Mock(), Mock()
        self.g.wait.side_effect = [None, FocusLost('失焦')]
        with self.assertRaises(FocusLost) as caught:
            self.g.click([10, 20])
        self.assertIs(caught.exception.input_started, True)
        flags = [call.args[1]._obj.value.mi.dwFlags for call in self.user.SendInput.call_args_list]
        self.assertEqual(flags, [2, 4])
        self.assertEqual(self.g._held_inputs, set())
        self.user.SetForegroundWindow.assert_not_called()


class FocusRunnerTest(unittest.TestCase):
    def test_scan_interruption_restores_focus_without_a_dialog_and_safely_rescans(self):
        r = runner()
        r.phase, r.batch = 'fill', [equipment(0, '稀有')]
        r.e.scan = Mock(side_effect=[FocusLost('失焦'), []])
        r.on_pause = Mock()
        with patch('streaming.sort_button', return_value=[422, 597]):
            r.run()
        r.e.game.recover_focus.assert_called_once()
        r.e.return_to_inventory.assert_called_once_with(allowed={0})
        r.e.game.activate.assert_not_called()
        r.on_pause.assert_not_called()
        self.assertEqual(r.phase, 'done')

    def test_focus_loss_after_selection_recovers_unrecorded_slot_without_adding_again(self):
        r = runner()
        r.phase = 'fill'
        r.e.scan = Mock(side_effect=[lambda: None])
        calls = [0]
        def scan(on_item):
            calls[0] += 1
            if calls[0] == 1:
                on_item(equipment(0, '稀有'))
        r.e.scan.side_effect = scan
        r.e.settled_board.side_effect = FocusLost('失焦')
        r.on_pause = Mock()
        with patch('streaming.sort_button', return_value=[422, 597]):
            r.run()
        r.e.return_to_inventory.assert_called_once_with(allowed={0})
        r.e.game.right_click.assert_called_once_with([100, 200], alt=True)
        r.on_pause.assert_not_called()

    def test_focus_loss_before_commit_retries_only_the_unsent_click(self):
        for mode in ('corrode', 'synthesize'):
            with self.subTest(mode=mode):
                r = runner()
                r.mode, r.phase = mode, mode + '_click'
                r.e.occupied.return_value = [0] if mode == 'corrode' else list(range(9))
                r.batch = ([equipment(0, '稀有')] if mode == 'corrode' else
                           [equipment(n, '传奇') for n in range(9)])
                r.counts = [1] * len(r.batch)
                lost = FocusLost('鼠标按下前失焦')
                lost.input_action, lost.input_started = 'left_click', False
                r.e.game.click.side_effect = [lost, None]
                original = r.step
                def step():
                    if r.phase == mode + '_result':
                        r.phase = 'done'
                    else:
                        original()
                r.step = step
                r.on_pause = Mock(side_effect=AssertionError('Unexpected pause'))
                r.run()
                self.assertEqual(r.e.game.click.call_count, 2)
                r.e.game.recover_focus.assert_called_once()
                r.on_pause.assert_not_called()

    def test_focus_loss_after_commit_keeps_result_phase_and_never_replays_click(self):
        for mode in ('corrode', 'synthesize'):
            with self.subTest(mode=mode):
                r = runner()
                r.mode, r.phase = mode, mode + '_click'
                r.e.occupied.return_value = [0] if mode == 'corrode' else list(range(9))
                r.batch = ([equipment(0, '稀有')] if mode == 'corrode' else
                           [equipment(n, '传奇') for n in range(9)])
                r.counts = [1] * len(r.batch)
                lost = FocusLost('发送后失焦')
                lost.input_action, lost.input_started = 'left_click', True
                r.e.game.click.side_effect = lost
                original = r.step
                def step():
                    if r.phase == mode + '_result':
                        r.phase = 'done'
                    else:
                        original()
                r.step = step
                r.on_pause = Mock(side_effect=AssertionError('Unexpected pause'))
                r.run()
                r.e.game.click.assert_called_once()
                r.on_pause.assert_not_called()

    def test_focus_loss_while_waiting_for_corrosion_result_never_submits_again(self):
        r = runner()
        r.phase, r.batch, r.counts = 'corrode_result', [equipment(0, '稀有')], [1]
        r.e.wait_corrode_progress = Mock(side_effect=[FocusLost('失焦'), ([0], [1])])
        r.e.scan = Mock(return_value=[])
        r.on_pause = Mock()
        with patch('streaming.sort_button', return_value=[422, 597]):
            r.run()
        self.assertEqual(r.corroded, 1)
        r.e.game.click.assert_not_called()
        r.on_pause.assert_not_called()

    def test_failed_recovery_uses_existing_confirmation_then_resumes(self):
        r = runner()
        r.phase = 'fill'
        r.e.scan = Mock(side_effect=[FocusLost('失焦'), []])
        r.e.game.recover_focus.side_effect = FocusRecoveryFailed('恢复失败')
        r.on_pause = Mock()
        with patch('streaming.sort_button', return_value=[422, 597]):
            r.run()
        r.e.game.recover_focus.assert_called_once()
        r.on_pause.assert_called_once_with('恢复失败')
        r.e.game.activate.assert_called_once()

    def test_exhausted_low_level_recovery_is_not_retried_by_runner(self):
        r = runner()
        r.phase = 'fill'
        r.e.scan = Mock(side_effect=[FocusRecoveryFailed('恢复失败'), []])
        r.on_pause = Mock()
        with patch('streaming.sort_button', return_value=[422, 597]):
            r.run()
        r.e.game.recover_focus.assert_not_called()
        r.on_pause.assert_called_once_with('恢复失败')

    def test_f10_or_game_closed_during_recovery_are_terminal(self):
        for error in (UserStopped('F10'), GameClosed('进程关闭')):
            with self.subTest(error=error):
                r = runner()
                r.e.ensure_panels.side_effect = FocusLost('失焦')
                r.e.game.recover_focus.side_effect = error
                r.on_pause = Mock()
                with self.assertRaises(type(error)):
                    r.run()
                r.on_pause.assert_not_called()
                r.e.game.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
