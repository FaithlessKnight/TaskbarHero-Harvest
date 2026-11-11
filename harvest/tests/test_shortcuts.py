import ctypes
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Engine
from rules import classify, corrosion_selection, parse_required_level
from windows_io import Frame, Game, Input, KeyboardInput
from test_engine import fake_engine


class ShortcutTest(unittest.TestCase):
    def game(self):
        game = Game.__new__(Game)
        for name in ('move', 'wait', 'require_target', 'right_button', 'key', 'require_modifier'):
            setattr(game, name, Mock())
        return game

    def test_alt_is_held_for_right_click_and_released_after_button(self):
        game = self.game()
        trace = Mock()
        trace.attach_mock(game.key, 'key')
        trace.attach_mock(game.right_button, 'right')
        with patch('windows_io.user') as user:
            user.GetAsyncKeyState.return_value = 0
            game.right_click([10, 20], alt=True)
        self.assertEqual([(c[0], c.args) for c in trace.mock_calls], [
            ('key', (0x12, True)), ('right', (True,)), ('right', (False,)), ('key', (0x12, False))])

    def test_plain_right_click_returns_without_any_modifier(self):
        game = self.game()
        with patch('windows_io.user') as user:
            user.GetAsyncKeyState.return_value = 0
            game.right_click([10, 20])
        self.assertEqual([c.args[0] for c in game.right_button.call_args_list], [True, False])
        game.key.assert_not_called()

    def test_stop_during_click_releases_right_button_and_alt(self):
        game = self.game()
        game.wait.side_effect = [None, None, RuntimeError('F10')]
        with patch('windows_io.user') as user:
            user.GetAsyncKeyState.return_value = 0
            with self.assertRaisesRegex(RuntimeError, 'F10'):
                game.right_click([10, 20], alt=True)
        game.right_button.assert_any_call(False)
        game.key.assert_called_with(0x12, False)

    def test_stop_after_alt_before_right_button_still_releases_alt(self):
        game = self.game()
        game.wait.side_effect = [None, RuntimeError('F10')]
        with patch('windows_io.user') as user:
            user.GetAsyncKeyState.return_value = 0
            with self.assertRaisesRegex(RuntimeError, 'F10'):
                game.right_click([10, 20], alt=True)
        game.right_button.assert_not_called()
        game.key.assert_called_with(0x12, False)

    def test_held_physical_modifier_prevents_wrong_game_shortcut(self):
        game = self.game()
        with patch('windows_io.user') as user:
            user.GetAsyncKeyState.return_value = 0x8000
            with self.assertRaisesRegex(RuntimeError, '松开'):
                game.right_click([10, 20], alt=True)
        game.right_button.assert_not_called()
        game.key.assert_not_called()

    def test_sendinput_keyboard_abi_and_right_button_flags(self):
        self.assertEqual(ctypes.sizeof(Input), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        self.assertEqual(ctypes.sizeof(KeyboardInput), 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)
        game = Game.__new__(Game)
        game.check = Mock()
        events = []
        def send(count, pointer, size):
            event = pointer._obj
            events.append((event.type, event.value.ki.wVk, event.value.ki.wScan, event.value.ki.dwFlags)
                          if event.type == 1 else (0, event.value.mi.dwFlags))
            return 1
        with patch('windows_io.user') as user:
            user.SendInput.side_effect = send
            game.key(0x12, True)
            game.right_button(True)
            game.right_button(False)
            game.key(0x12, False)
        self.assertEqual(events, [(1, 0, 0x38, 8), (0, 8), (0, 16), (1, 0, 0x38, 10)])

    def test_ctrl_and_alt_have_distinct_left_key_scan_codes(self):
        game = Game.__new__(Game)
        game.check = Mock()
        events = []
        def send(count, pointer, size):
            key = pointer._obj.value.ki
            events.append((key.wVk, key.wScan, key.dwFlags))
            return 1
        with patch('windows_io.user') as user:
            user.SendInput.side_effect = send
            game.key(0x11, True)
            game.key(0x11, False)
            game.key(0x12, True)
            game.key(0x12, False)
        self.assertEqual(events, [(0, 0x1D, 8), (0, 0x1D, 10), (0, 0x38, 8), (0, 0x38, 10)])

    def test_wrong_modifier_state_blocks_right_click_and_releases_ctrl(self):
        game = self.game()
        game.require_modifier = Game.require_modifier.__get__(game)
        state = {'pressed': False}
        game.key.side_effect = lambda vk, down: state.update(pressed=down)
        with patch('windows_io.user') as user:
            user.GetAsyncKeyState.side_effect = lambda vk: 0x8000 if state['pressed'] and vk == 0x12 else 0
            with self.assertRaisesRegex(RuntimeError, '修饰键状态未确认'):
                game.right_click([10, 20], ctrl=True)
        game.right_button.assert_not_called()
        game.key.assert_called_with(0x11, False)

    def test_expected_ctrl_state_is_confirmed_before_mouse_down(self):
        game = self.game()
        game.require_modifier = Game.require_modifier.__get__(game)
        state = {'pressed': False}
        game.key.side_effect = lambda vk, down: state.update(pressed=down)
        with patch('windows_io.user') as user:
            user.GetAsyncKeyState.side_effect = lambda vk: 0x8000 if state['pressed'] and vk == 0x11 else 0
            game.right_click([10, 20], ctrl=True)
        self.assertEqual([c.args[0] for c in game.right_button.call_args_list], [True, False])


class RecognitionFixTest(unittest.TestCase):
    def test_all_three_material_labels_accept_observed_icon_zero(self):
        for label in ('0 制 作 材 料', '0 装 饰 材 料', '0 雕 刻 材 料', '材料'):
            item = classify(0, ['不朽级', label, '攻击伤害+30'])
            self.assertEqual((item.action, item.kind), ('corrode', 'material'))
        self.assertEqual(classify(0, ['普通级', '制作材料', '供奉材料']).action, 'store')

    def test_observed_orb_ocr_is_equipment_without_a_damage_stat(self):
        item = classify(0, ['次元宝珠', '稀有級', '冷却缩减+24.00%', '0装饰稽'])
        self.assertEqual((item.action, item.kind), ('corrode', 'equipment'))
        self.assertEqual(classify(0, ['稀有级', '可提高装饰稽']).action, 'keep')

    def test_required_level_ignores_stats_prices_icons_and_newline_shortcuts(self):
        for text in ('攻击伤害439\n需要等级80\n302400', '需 要 等 8 0', '需 苤 等 80'):
            self.assertEqual(parse_required_level(text), 80)
        for text in ('Lv.90', '防御力80', '需要等级\n1 显示快捷键', '需要等级0', '需要等级1000',
                     '需要等级80\n需要等级90'):
            self.assertIsNone(parse_required_level(text))
        item = classify(0, ['传奇级', '防御力439', '需要等级80'], icon_text='90')
        self.assertEqual((item.action, item.level), ('synthesize', 80))

    def test_missing_required_level_does_not_fall_back_to_icon(self):
        item = classify(0, ['传奇级', '防御力439', '价格302400'], icon_text='90')
        self.assertEqual(item.action, 'synthesize')
        self.assertIsNone(item.level)

    def test_level_crop_requires_label_and_two_matching_digit_readings(self):
        frame = Frame(224, 300, bytes([0, 0, 0, 255]) * 224 * 300)
        details = [{'text': '需要等级', 'words': [{'text': '需要等级', 'box': [150, 230, 40, 10]}]}]
        for texts, expected in ((['80', '80'], 80), (['80', '90'], None),
                                (['80', ''], None), (['需要等级80', '80'], None)):
            engine = fake_engine()
            engine.read_saved = Mock(side_effect=[({'text': t}, 'digits.bmp') for t in texts])
            self.assertEqual(engine.tooltip_level(frame, [{'details': details}], 'test'), expected)
        engine = fake_engine()
        engine.read_saved = Mock()
        self.assertIsNone(engine.tooltip_level(frame, [{'text': '80'}], 'test'))
        engine.read_saved.assert_not_called()

    def test_legacy_material_boxes_merge_by_union_and_new_explicit_choice_wins(self):
        old = {'equipment': ['稀有'], 'crafting': ['传奇'], 'decoration': ['不朽'], 'engraving': ['至宝']}
        self.assertEqual(corrosion_selection(old), {'equipment': ['稀有'], 'material': ['传奇', '不朽', '至宝']})
        old['material'] = []
        self.assertEqual(corrosion_selection(old)['material'], [])


class ReturnItemsTest(unittest.TestCase):
    def engine(self):
        engine = fake_engine()
        frame = Frame(1, 1, bytes([0, 0, 0, 255]))
        other = Frame(1, 1, bytes([255, 255, 255, 255]))
        engine.game.frame.return_value = frame
        engine.config['regions'] = {'mode_label': [0, 0, 1, 1]}
        engine.config['learned']['corrode'] = {'mode_label': frame.template()}
        engine.config['learned']['synthesize']['mode_label'] = other.template()
        engine.occupied = Mock(return_value=[0, 2])
        engine.settled_board = Mock(side_effect=[(frame, [0, 2]), (frame, [2]), (frame, [])])
        return engine

    def test_returns_each_cube_item_by_plain_right_click_without_commit(self):
        engine = self.engine()
        engine.return_to_inventory()
        self.assertEqual([c.args[0] for c in engine.game.right_click.call_args_list], [[10, 10], [12, 10]])
        self.assertTrue(all(not c.kwargs for c in engine.game.right_click.call_args_list))
        engine.game.drag.assert_not_called()
        engine.game.click.assert_not_called()

    def test_full_bag_or_unconfirmed_return_stops_before_next_item(self):
        engine = self.engine()
        engine.settled_board.side_effect = None
        engine.settled_board.return_value = (engine.game.frame(), [0, 2])
        with self.assertRaisesRegex(RuntimeError, '右键取回未确认'):
            engine.return_to_inventory()
        engine.game.right_click.assert_called_once()
        engine.game.click.assert_not_called()

    def test_selective_return_preserves_the_other_batch_slots(self):
        engine = self.engine()
        frame = engine.game.frame()
        engine.settled_board.side_effect = [(frame, [0, 2]), (frame, [0])]
        engine.return_to_inventory(slots={2}, allowed={0, 2})
        engine.game.right_click.assert_called_once_with([12, 10])
        engine.game.click.assert_not_called()

    def test_resume_never_returns_untracked_slots(self):
        engine = self.engine()
        with self.assertRaisesRegex(RuntimeError, '本批记录之外'):
            engine.return_to_inventory(allowed={0})
        engine.game.right_click.assert_not_called()
        engine.game.click.assert_not_called()
        engine.save_state.assert_called_once_with('cube-return-unknown')

    def test_manually_returned_batch_slot_is_accepted_on_resume(self):
        engine = self.engine()
        frame = engine.game.frame()
        engine.settled_board.side_effect = [(frame, [2]), (frame, [])]
        engine.return_to_inventory(allowed={0, 2})
        engine.game.right_click.assert_called_once_with([12, 10])


if __name__ == '__main__':
    unittest.main()
