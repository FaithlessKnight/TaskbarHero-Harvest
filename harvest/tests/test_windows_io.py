import ctypes
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import cell_box, grid, validate
from windows_io import Frame, Game, GameClosed, Input, UserStopped, movement_input


class ImageTest(unittest.TestCase):
    def test_native_input_structure_has_windows_abi_size(self):
        self.assertEqual(ctypes.sizeof(Input), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)

    def test_absolute_movement_maps_pixel_centers_in_virtual_desktop(self):
        event = movement_input(-640, 540, (-1280, 0, 3200, 1080))
        self.assertEqual(event.value.mi.dwFlags, 0xE001)
        self.assertEqual(event.value.mi.dx * 3200 // 65536 - 1280, -640)
        self.assertEqual(event.value.mi.dy * 1080 // 65536, 540)
        for x, y in ((-1280, 0), (1919, 1079)):
            event = movement_input(x, y, (-1280, 0, 3200, 1080))
            self.assertTrue(0 <= event.value.mi.dx <= 65535)
            self.assertTrue(0 <= event.value.mi.dy <= 65535)
        with self.assertRaises(RuntimeError):
            movement_input(1920, 540, (-1280, 0, 3200, 1080))

    def test_hover_retry_moves_inside_item_without_clicking(self):
        game = Game.__new__(Game)
        game.move, game.wait, game.click, game.drag = Mock(), Mock(), Mock(), Mock()
        game.hover([100, 200], 2, retry=True)
        self.assertEqual([c.args[0] for c in game.move.call_args_list], [[102, 201], [100, 200]])
        game.wait.assert_any_call(2)
        game.click.assert_not_called()
        game.drag.assert_not_called()

    def test_click_dwells_holds_and_waits_after_release(self):
        game = Game.__new__(Game)
        game.move, game.wait, game.require_target, game.button = Mock(), Mock(), Mock(), Mock()
        trace = Mock()
        trace.attach_mock(game.move, 'move')
        trace.attach_mock(game.wait, 'wait')
        trace.attach_mock(game.button, 'button')
        game.click([100, 200])
        self.assertEqual([(c[0], c.args) for c in trace.mock_calls], [
            ('move', ([100, 200],)), ('wait', (0.25,)), ('button', (True,)),
            ('wait', (0.35,)), ('button', (False,)), ('wait', (0.4,))])

    def test_interrupted_click_releases_button(self):
        game = Game.__new__(Game)
        game.move, game.require_target, game.button = Mock(), Mock(), Mock()
        game.wait = Mock(side_effect=[None, RuntimeError('已停止')])
        with self.assertRaisesRegex(RuntimeError, '已停止'):
            game.click([100, 200])
        self.assertEqual([c.args[0] for c in game.button.call_args_list], [True, False])

    def test_drag_allows_transparent_route_but_checks_both_endpoints(self):
        game = Game.__new__(Game)
        game.move, game.wait, game.button = Mock(), Mock(), Mock()
        game.require_target, game.check = Mock(), Mock()
        game.drag([100, 200], [400, 300])
        self.assertEqual(game.move.call_args_list[0].args[0], [100, 200])
        self.assertEqual(game.require_target.call_count, 2)
        game.require_target.assert_called_with([400, 300])
        for call in game.move.call_args_list[1:]:
            self.assertTrue(call.kwargs['dragging'])
        self.assertEqual([c.args[0] for c in game.button.call_args_list], [True, False])

    def test_obstructed_destination_aborts_before_mouse_down(self):
        game = Game.__new__(Game)
        game.move, game.wait, game.button = Mock(), Mock(), Mock()
        game.require_target = Mock(side_effect=RuntimeError('目标位置被其他窗口遮挡'))
        with self.assertRaisesRegex(RuntimeError, '遮挡'):
            game.drag([100, 200], [400, 300])
        game.button.assert_not_called()

    def test_stop_during_drag_always_releases_button(self):
        game = Game.__new__(Game)
        game.move = Mock(side_effect=[None, RuntimeError('已停止')])
        game.wait, game.button, game.require_target = Mock(), Mock(), Mock()
        with self.assertRaisesRegex(RuntimeError, '已停止'):
            game.drag([100, 200], [400, 300])
        self.assertEqual([c.args[0] for c in game.button.call_args_list], [True, False])

    def test_wheel_encodes_down_delta_without_clicking(self):
        game = Game.__new__(Game)
        game.move, game.wait, game.check, game.require_target = Mock(), Mock(), Mock(), Mock()
        with patch('windows_io.user') as user:
            user.SendInput.return_value = 1
            game.scroll([100, 200], -10)
            event = user.SendInput.call_args.args[1]._obj
            self.assertEqual(event.value.mi.dwFlags, 0x0800)
            self.assertEqual(event.value.mi.mouseData, 0xFFFFFFF6)
        game.require_target.assert_called_once_with([100, 200])

    def test_empty_slot_requires_dark_neutral_pixels(self):
        dark = Frame(20, 20, bytes([23, 23, 23, 255]) * 400)
        self.assertTrue(dark.empty_inventory_slot())
        self.assertFalse(Frame(20, 20, bytes([20, 20, 42, 255]) * 400).empty_inventory_slot())
        self.assertFalse(Frame(20, 20, bytes([50, 50, 50, 255]) * 400).empty_inventory_slot())

    def test_move_sends_input_and_checks_actual_position(self):
        game = Game.__new__(Game)
        game.hwnd = 123
        game.check, game.rect = Mock(), Mock(return_value=(100, 200, 500, 500))
        with patch('windows_io.user') as user:
            user.GetAncestor.return_value = 123
            user.GetSystemMetrics.side_effect = [0, 0, 1920, 1080]
            user.SendInput.return_value = 1

            def position(pointer):
                pointer._obj.x, pointer._obj.y = 150, 260
                return True
            user.GetCursorPos.side_effect = position
            game.move([50, 60])
            self.assertEqual(game.expected_cursor, (150, 260))
            event = user.SendInput.call_args.args[1]._obj
            self.assertEqual(event.value.mi.dwFlags, 0xE001)

    def test_rejected_input_stops_without_fallback_click(self):
        game = Game.__new__(Game)
        game.hwnd = 123
        game.check, game.rect = Mock(), Mock(return_value=(100, 200, 500, 500))
        with patch('windows_io.user') as user:
            user.GetAncestor.return_value = 123
            user.GetSystemMetrics.side_effect = [0, 0, 1920, 1080]
            user.SendInput.return_value = 0
            with self.assertRaisesRegex(RuntimeError, '移动输入被 Windows 拒绝'):
                game.move([50, 60])
            self.assertEqual(user.SendInput.call_count, 1)

    def test_move_waits_for_queued_input_to_reach_position(self):
        game = Game.__new__(Game)
        game.hwnd, game.stop = 123, threading.Event()
        game.check, game.wait = Mock(), Mock()
        game.rect = Mock(return_value=(100, 200, 500, 500))
        positions = iter([(200, 300), (180, 280), (150, 260)])
        with patch('windows_io.user') as user:
            user.GetAncestor.return_value = user.GetForegroundWindow.return_value = 123
            user.GetSystemMetrics.side_effect = [0, 0, 1920, 1080]
            user.SendInput.return_value = 1
            user.IsIconic.return_value = False
            user.GetAsyncKeyState.return_value = 0

            def position(pointer):
                pointer._obj.x, pointer._obj.y = next(positions)
                return True
            user.GetCursorPos.side_effect = position
            game.move([50, 60])
        self.assertEqual(game.expected_cursor, (150, 260))
        self.assertEqual(game.wait.call_count, 2)
        self.assertEqual(len(game.last_move['samples']), 3)

    def test_move_timeout_contains_target_and_actual_and_never_clicks(self):
        game = Game.__new__(Game)
        game.hwnd, game.stop = 123, threading.Event()
        game.check, game.wait = Mock(), Mock()
        game.rect = Mock(return_value=(100, 200, 500, 500))
        with patch('windows_io.user') as user:
            user.GetAncestor.return_value = user.GetForegroundWindow.return_value = 123
            user.GetSystemMetrics.side_effect = [0, 0, 1920, 1080]
            user.SendInput.return_value = 1
            user.IsIconic.return_value = False
            user.GetAsyncKeyState.return_value = 0

            def position(pointer):
                pointer._obj.x, pointer._obj.y = 200, 300
                return True
            user.GetCursorPos.side_effect = position
            with self.assertRaisesRegex(RuntimeError, r'目标 \(150, 260\).*实际 \(200, 300\)'):
                game.move([50, 60])
            self.assertEqual(user.SendInput.call_count, 1)
        self.assertEqual(len(game.last_move['samples']), 31)

    def test_pointer_wait_respects_stop(self):
        game = Game.__new__(Game)
        game.hwnd, game.stop = 123, threading.Event()
        game.stop.set()
        game.check, game.wait = Mock(), Mock()
        game.rect = Mock(return_value=(100, 200, 500, 500))
        with patch('windows_io.user') as user:
            user.GetAncestor.return_value = 123
            user.GetSystemMetrics.side_effect = [0, 0, 1920, 1080]
            user.SendInput.return_value = 1
            user.GetCursorPos.return_value = False
            with self.assertRaisesRegex(RuntimeError, '已停止'):
                game.move([50, 60])
        game.wait.assert_not_called()

    def test_tooltip_detection_ignores_static_black_background(self):
        background = Frame(240, 240, bytes([80, 90, 100, 255]) * 240 * 240)
        changed = bytearray(background.pixels)
        for y in range(60, 180):
            for x in range(40, 150):
                changed[(y * 240 + x) * 4:(y * 240 + x) * 4 + 4] = bytes([0, 0, 0, 255])
        after = Frame(240, 240, bytes(changed))
        self.assertEqual(after.tooltip_boxes(after, [170, 130], 30), [])
        boxes = after.tooltip_boxes(background, [170, 130], 30)
        self.assertTrue(boxes)
        x, y, w, h = boxes[0]
        self.assertTrue(x <= 40 and y <= 60 and x + w >= 150 and y + h >= 180)

    def test_cropped_region_preserves_bgra_rows(self):
        frame = Frame(3, 2, bytes(range(24)))
        self.assertEqual(frame.crop([1, 0, 1, 2]).pixels, bytes(range(4, 8)) + bytes(range(16, 20)))
        with self.assertRaises(RuntimeError):
            frame.crop([2, 0, 2, 1])

    def test_separate_comparison_panel_does_not_enter_nearest_crop(self):
        background = Frame(400, 300, bytes([90, 90, 90, 255]) * 400 * 300)
        pixels = bytearray(background.pixels)
        # Larger equipped panel, then the current item's header/body/footer.
        for x0, y0, x1, y1 in ((20, 10, 160, 280), (170, 50, 310, 70),
                               (170, 76, 310, 190), (170, 196, 310, 220)):
            for y in range(y0, y1):
                for x in range(x0, x1):
                    offset = (y * 400 + x) * 4
                    pixels[offset:offset + 4] = bytes([0, 0, 0, 255])
        after = Frame(400, 300, bytes(pixels))
        for pointer in ([330, 130], [150, 200]):
            boxes = after.tooltip_boxes(background, pointer, 30)
            self.assertTrue(boxes)
            x, y, w, h = boxes[0]
            if pointer[0] == 330:
                self.assertTrue(x >= 170 and x + w <= 310)
                self.assertTrue(y <= 50 and y + h >= 220)
            else:
                self.assertTrue(x <= 20 and x + w <= 160)

    def test_distant_comparison_does_not_substitute_for_current_tooltip(self):
        background = Frame(400, 300, bytes([90, 90, 90, 255]) * 400 * 300)
        pixels = bytearray(background.pixels)
        for y in range(20, 280):
            for x in range(20, 160):
                offset = (y * 400 + x) * 4
                pixels[offset:offset + 4] = bytes([0, 0, 0, 255])
        self.assertEqual(Frame(400, 300, bytes(pixels)).tooltip_boxes(background, [330, 130], 30), [])

    def test_ocr_padding_contains_no_neighbour_pixels(self):
        frame = Frame(2, 1, bytes([90, 80, 70, 255]) * 2)
        padded = frame.padded()
        self.assertEqual((padded.width, padded.height), (10, 9))
        self.assertEqual(padded.crop([4, 4, 2, 1]).pixels, frame.pixels)
        self.assertEqual(padded.pixels[:4], bytes([0, 0, 0, 255]))

    def test_icon_pixels_are_not_classified_as_empty(self):
        empty = Frame(30, 30, bytes([10, 10, 10, 255]) * 900)
        occupied = Frame(30, 30, bytes([150, 100, 90, 255]) * 900)
        self.assertTrue(empty.matches(empty.template()))
        self.assertFalse(occupied.matches(empty.template()))

    def test_small_animated_pixel_does_not_change_slot_state(self):
        empty = Frame(30, 30, bytes([10, 10, 10, 255]) * 900)
        pixels = bytearray(empty.pixels)
        pixels[:4] = bytes([20, 20, 20, 255])
        self.assertTrue(Frame(30, 30, bytes(pixels)).matches(empty.template()))

    def test_grid_uses_all_rows_and_columns(self):
        config = {'points': {'inventory_first': [100, 200], 'inventory_last': [340, 280]},
                  'bag_columns': 7, 'bag_rows': 3}
        points = grid(config, 'inventory', 7, 3)
        self.assertEqual((len(points), points[0], points[-1], points[7]), (21, [100, 200], [340, 280], [100, 240]))
        self.assertEqual(cell_box(config, 'inventory', points[0]), [84, 184, 32, 32])

    def test_execution_requires_learned_modes(self):
        with self.assertRaisesRegex(RuntimeError, '尚未校准'):
            validate({'points': {}, 'regions': {}, 'learned': {}}, True)


class ClickThroughTest(unittest.TestCase):
    """Simulate Unity enabling input only after a new instance is hovered."""

    def setUp(self):
        self.game = Game.__new__(Game)
        self.game.hwnd, self.game.pid = 123, 100
        self.game.stop = threading.Event()
        self.game.original_size = (500, 500)
        self.game.expected_cursor = None
        self.game.rect = Mock(return_value=(100, 200, 500, 500))
        self.game.wait = Mock()
        self.moved, self.cursor, self.flags = False, (900, 500), []
        user_patch, pid_patch = patch('windows_io.user'), patch('windows_io.window_pid', return_value=100)
        self.user, self.pid = user_patch.start(), pid_patch.start()
        self.addCleanup(user_patch.stop)
        self.addCleanup(pid_patch.stop)
        self.user.IsWindow.return_value = True
        self.user.IsIconic.return_value = False
        self.user.GetForegroundWindow.return_value = 123
        self.user.GetAsyncKeyState.return_value = 0
        metrics = dict(zip((76, 77, 78, 79), (0, 0, 1920, 1080)))
        self.user.GetSystemMetrics.side_effect = metrics.__getitem__
        self.user.GetAncestor.side_effect = lambda hwnd, kind: hwnd
        self.user.WindowFromPoint.return_value = 123

        def position(pointer):
            pointer._obj.x, pointer._obj.y = self.cursor
            return True

        def send(count, pointer, size):
            flags = pointer._obj.value.mi.dwFlags
            self.flags.append(flags)
            if flags == 0xE001:
                self.moved, self.cursor = True, (150, 260)
            return count

        self.user.GetCursorPos.side_effect = position
        self.user.SendInput.side_effect = send

    def test_switching_instance_moves_first_then_waits_for_click_through_to_clear(self):
        owners = iter((None, 999, 123))

        def hit(point):
            self.assertTrue(self.moved, 'Hit testing before the move cannot wake a click-through game')
            self.assertEqual((point.x, point.y), self.cursor)
            return next(owners)

        self.user.WindowFromPoint.side_effect = hit
        self.game.move([50, 60])
        self.assertEqual(self.game.expected_cursor, (150, 260))
        self.assertEqual(self.flags, [0xE001])
        self.assertEqual([c.args for c in self.game.wait.call_args_list], [(0.02,), (0.02,)])

    def test_candidate_probe_accepts_game_only_after_hover_updates_hit_testing(self):
        self.user.WindowFromPoint.side_effect = [None, 999, 123]
        self.assertTrue(self.game.can_target([50, 60]))
        self.assertEqual(self.flags, [0xE001])
        self.assertEqual(self.game.wait.call_count, 2)

    def test_candidate_probe_rejects_other_windows_without_button_input(self):
        for owner in (None, 999):
            with self.subTest(owner=owner):
                self.flags.clear()
                self.user.WindowFromPoint.return_value = owner
                self.assertFalse(self.game.can_target([50, 60]))
                self.assertEqual(self.flags, [0xE001])

    def test_f10_during_candidate_probe_propagates_instead_of_rejecting_candidate(self):
        self.user.WindowFromPoint.return_value = None
        self.game.wait.side_effect = lambda seconds: self.game.stop.set()
        with self.assertRaises(UserStopped):
            self.game.can_target([50, 60])
        self.assertEqual(self.flags, [0xE001])

    def test_persistent_transparency_or_other_game_never_receives_a_click(self):
        for owner in (None, 999):
            with self.subTest(owner=owner):
                self.flags.clear()
                self.game.wait.reset_mock()
                self.user.WindowFromPoint.return_value = owner
                with self.assertRaisesRegex(RuntimeError, '目标位置被其他窗口遮挡'):
                    self.game.click([50, 60])
                self.assertEqual(self.flags, [0xE001])
                self.assertEqual(self.game.wait.call_count, 20)

    def test_obstruction_appearing_after_hover_is_rejected_before_button_down(self):
        def wait(seconds):
            if seconds == 0.25:
                self.user.WindowFromPoint.return_value = 999

        self.game.wait.side_effect = wait
        with self.assertRaisesRegex(RuntimeError, '遮挡'):
            self.game.click([50, 60])
        self.assertEqual(self.flags, [0xE001])
        self.assertEqual(self.game.wait.call_count, 1)

    def test_f10_interrupts_hit_test_wait_without_clicking(self):
        self.user.WindowFromPoint.return_value = None
        self.game.wait.side_effect = lambda seconds: self.game.stop.set()
        with self.assertRaises(UserStopped):
            self.game.click([50, 60])
        self.assertEqual(self.flags, [0xE001])
        self.game.wait.assert_called_once_with(0.02)

    def test_focus_loss_interrupts_hit_test_wait_without_clicking(self):
        self.user.WindowFromPoint.return_value = None
        self.game.wait.side_effect = lambda seconds: setattr(self.user.GetForegroundWindow, 'return_value', 999)
        with self.assertRaisesRegex(RuntimeError, '前台焦点'):
            self.game.click([50, 60])
        self.assertEqual(self.flags, [0xE001])
        self.game.wait.assert_called_once_with(0.02)

    def test_pid_reuse_interrupts_hit_test_wait_without_clicking(self):
        self.user.WindowFromPoint.return_value = None
        self.game.wait.side_effect = lambda seconds: setattr(self.pid, 'return_value', 200)
        with self.assertRaises(GameClosed):
            self.game.click([50, 60])
        self.assertEqual(self.flags, [0xE001])
        self.game.wait.assert_called_once_with(0.02)

    def test_out_of_bounds_target_does_not_move_or_click(self):
        with self.assertRaisesRegex(RuntimeError, '超出游戏窗口'):
            self.game.click([500, 60])
        self.assertEqual(self.flags, [])
        self.user.WindowFromPoint.assert_not_called()

    def test_drag_movement_still_crosses_transparent_background(self):
        self.user.WindowFromPoint.return_value = None
        self.game.move([50, 60], dragging=True)
        self.assertEqual(self.flags, [0xE001])
        self.user.WindowFromPoint.assert_not_called()
        self.game.wait.assert_not_called()


if __name__ == '__main__':
    unittest.main()
