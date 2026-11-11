"""Startup space key and multi-instance sequencing, with simulated input only."""
import ctypes
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sessions import harvest_all
from streaming import StreamRunner
from windows_io import Game, GameClosed, GameTarget, FocusLost, Input, UserStopped
from test_streaming import runner


def space_game():
    game = Game.__new__(Game)
    game.ready = Mock()
    game.check = Mock()
    game.wait = Mock()
    game.frame = Mock()
    return game


class StartupSpaceTest(unittest.TestCase):
    def setUp(self):
        isolation = patch('sessions.GameIsolation')
        isolation.start()
        self.addCleanup(isolation.stop)
        self.user_patch = patch('windows_io.user')
        self.user = self.user_patch.start()
        self.addCleanup(self.user_patch.stop)
        self.user.GetAsyncKeyState.return_value = 0
        self.events = []

        def send(count, pointer, size):
            event = ctypes.cast(pointer, ctypes.POINTER(Input)).contents
            self.events.append((event.type, event.value.ki.wVk, event.value.ki.dwFlags))
            return 1

        self.user.SendInput.side_effect = send

    def test_one_unmodified_space_press_and_release_per_game_object(self):
        game = space_game()
        self.assertIs(game.press_space_once(), True)
        self.assertIs(game.press_space_once(), False)
        self.assertEqual(self.events, [(1, 0x20, 0), (1, 0x20, 2)])
        self.assertFalse(game._held_inputs)
        game.ready.assert_called_once()
        self.assertEqual([call.args for call in game.wait.call_args_list], [(0.1,), (0.4,)])
        other = space_game()
        self.assertIs(other.press_space_once(), True)
        self.assertEqual(len(self.events), 4)

    def test_f10_during_hold_releases_space_and_does_not_repeat_it(self):
        game = space_game()
        game.wait.side_effect = UserStopped('F10')
        with self.assertRaises(UserStopped):
            game.press_space_once()
        self.assertEqual(self.events, [(1, 0x20, 0), (1, 0x20, 2)])
        self.assertFalse(game._held_inputs)
        self.assertIs(game.press_space_once(), False)

    def test_focus_or_stop_failure_before_ready_sends_no_keyboard_input(self):
        for error in (FocusLost('失焦'), UserStopped('F10'), GameClosed('已关闭')):
            with self.subTest(error=type(error).__name__):
                game = space_game()
                game.ready.side_effect = error
                with self.assertRaises(type(error)):
                    game.press_space_once()
                self.assertFalse(getattr(game, '_harvest_space_sent', False))
        self.user.SendInput.assert_not_called()

    def test_focus_loss_before_key_down_remains_retryable(self):
        game = space_game()
        game.check.side_effect = [FocusLost('发送前失焦'), None]
        with self.assertRaises(FocusLost):
            game.press_space_once()
        self.assertFalse(getattr(game, '_harvest_space_sent', False))
        self.assertEqual(self.events, [(1, 0x20, 2)])
        self.assertIs(game.press_space_once(), True)
        self.assertEqual(self.events[-2:], [(1, 0x20, 0), (1, 0x20, 2)])

    def test_any_held_modifier_or_space_blocks_startup_key(self):
        for held in (0x10, 0x11, 0x12, 0x20):
            with self.subTest(held=held):
                self.user.GetAsyncKeyState.side_effect = lambda vk: 0x8000 if vk == held else 0
                with self.assertRaisesRegex(RuntimeError, '请松开'):
                    space_game().press_space_once()
        self.user.SendInput.assert_not_called()

    def test_rejected_key_down_does_not_mark_space_as_sent(self):
        game = space_game()
        self.user.SendInput.side_effect = [0, 1]
        with self.assertRaisesRegex(RuntimeError, '键盘输入'):
            game.press_space_once()
        self.assertFalse(getattr(game, '_harvest_space_sent', False))
        self.assertFalse(getattr(game, '_held_inputs', set()))

    def test_panel_open_pause_resumes_without_repeating_sent_space(self):
        r = runner()
        r.e.game = space_game()
        r.e.ensure_panels.side_effect = [RuntimeError('页面未打开'), None]
        with self.assertRaisesRegex(RuntimeError, '页面未打开'):
            r.step()
        r.step()
        self.assertEqual(r.phase, 'prepare_start')
        self.assertEqual(self.events, [(1, 0x20, 0), (1, 0x20, 2)])

    def test_same_game_focus_recovery_or_restart_of_stream_does_not_repeat(self):
        r = runner()
        r.e.game = space_game()
        r.step()
        r.e.game.ready()
        StreamRunner(r.e).step()
        self.assertEqual(self.events, [(1, 0x20, 0), (1, 0x20, 2)])

    def test_each_game_gets_space_before_opening_panels_then_next_game_starts(self):
        trace = []
        targets = [GameTarget(10, 100), GameTarget(20, 200)]

        def create(config, stop, log, progress, *, target):
            e = runner().e
            e.folder = Path('records') / str(target.pid)
            e.stop = stop
            e.game = space_game()
            e.game.ready.side_effect = lambda: trace.append(('ready', target.pid))
            e.game.wait.side_effect = lambda seconds: trace.append(('space-held' if seconds == .1 else 'space-released', target.pid))
            e.ensure_panels.side_effect = lambda: trace.append(('panels', target.pid))
            e.scan = Mock(return_value=[])
            e.execute_stream = lambda pause: StreamRunner(e, pause).run()
            e.close = Mock(side_effect=lambda: trace.append(('close', target.pid)))
            return e

        with patch('sessions.find_games', return_value=targets), patch('sessions.Engine', side_effect=create), \
                patch('streaming.sort_button', return_value=[422, 597]):
            result = harvest_all({}, threading.Event(), Mock(), Mock())
        self.assertEqual(result.completed, 2)
        self.assertEqual(trace, [(name, pid) for pid in (100, 200)
                                 for name in ('ready', 'space-held', 'space-released', 'panels', 'close')])
        self.assertEqual(self.events, [(1, 0x20, 0), (1, 0x20, 2)] * 2)


if __name__ == '__main__':
    unittest.main()
