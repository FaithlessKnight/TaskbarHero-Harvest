"""Cube function recovery using simulated menus, without real game input."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cube_modes import LABELS, menu_region, option, select_unknown
from vision import asset
from windows_io import Frame, UserStopped
from test_engine import fake_engine


class CubeModeTest(unittest.TestCase):
    def setUp(self):
        images = patch('cube_modes.image_options', return_value=[])
        images.start()
        self.addCleanup(images.stop)
        self.e = fake_engine()
        self.e.config['points'].update(mode_menu=[280, 28], mode_corrode=[220, 112], mode_synthesize=[220, 60])
        self.e.config['regions'] = {'mode_label': [200, 20, 40, 16]}
        self.colors = {'corrode': 20, 'synthesize': 200, 'unknown': 90}
        for mode in LABELS:
            self.e.config['learned'].setdefault(mode, {})['mode_label'] = Frame(
                40, 16, bytes([self.colors[mode]] * 3 + [255]) * 640).template()
        self.state = {'label': 'unknown', 'open': False}
        self.dynamic = {'corrode': [244, 120], 'synthesize': [244, 66]}
        self.e.game.frame.side_effect = self.snapshot
        self.e.game.click.side_effect = self.click
        self.e.read_saved = Mock(side_effect=self.read_menu)

    def snapshot(self):
        width, height = 360, 240
        pixels = bytearray(bytes([80, 80, 80, 255]) * width * height)
        for y in range(20, 36):
            start = (y * width + 200) * 4
            pixels[start:start + 40 * 4] = bytes([self.colors[self.state['label']]] * 3 + [255]) * 40
        return Frame(width, height, bytes(pixels))

    def click(self, point):
        if point == self.e.config['points']['mode_menu']:
            self.state['open'] = not self.state['open']
            return
        for mode in LABELS:
            if point in (self.dynamic[mode], self.e.config['points']['mode_' + mode]) and self.state['open']:
                self.state.update(label=mode, open=False)
                return
        self.fail('Unexpected click: ' + str(point))

    def line(self, mode, region, point=None):
        point = point or self.dynamic[mode]
        return {'text': ' '.join(LABELS[mode]), 'words': [
            {'text': LABELS[mode], 'box': [point[0] - region[0] - 14, point[1] - region[1] - 6, 28, 12]}]}

    def read_menu(self, frame, name, *, region, scale):
        details = [self.line(mode, region) for mode in LABELS] if self.state['open'] else []
        return {'details': details}, 'menu.bmp'

    def test_unknown_function_opens_dropdown_and_finds_requested_text_at_actual_position(self):
        self.e.mode('corrode')
        self.assertEqual([c.args[0] for c in self.e.game.click.call_args_list], [[280, 28], [244, 120]])
        self.assertEqual(self.e.current_mode, 'corrode')
        self.e.check_options.assert_called_once_with('corrode')
        self.e.game.right_click.assert_not_called()
        self.assertFalse(self.e._mode_menu_pending)

    def test_unknown_function_can_also_find_synthesis(self):
        self.e.mode('synthesize')
        self.assertEqual([c.args[0] for c in self.e.game.click.call_args_list], [[280, 28], [244, 66]])
        self.assertEqual(self.e.current_mode, 'synthesize')
        self.e.check_options.assert_called_once_with('synthesize')

    def test_recognized_requested_function_never_opens_the_menu(self):
        self.state['label'] = 'corrode'
        self.e.mode('corrode')
        self.e.game.click.assert_not_called()
        self.e.read_saved.assert_not_called()
        self.e.check_options.assert_called_once_with('corrode')

    def test_recognized_other_function_keeps_the_calibrated_switch(self):
        self.state['label'] = 'synthesize'
        self.e.mode('corrode')
        self.assertEqual([c.args[0] for c in self.e.game.click.call_args_list], [[280, 28], [220, 112]])
        self.e.read_saved.assert_not_called()
        self.assertEqual(self.e.current_mode, 'corrode')

    def test_known_nonempty_board_blocks_function_switch_before_any_click(self):
        self.state['label'] = 'synthesize'
        self.e.occupied.return_value = [4]
        with self.assertRaisesRegex(RuntimeError, '已有物品'):
            self.e.mode('corrode')
        self.e.game.click.assert_not_called()

    def test_unknown_mode_switch_still_requires_an_empty_board_after_selection(self):
        self.e.occupied.return_value = [4]
        with self.assertRaisesRegex(RuntimeError, '九宫格非空'):
            self.e.mode('corrode')
        self.assertEqual(self.e.game.click.call_count, 2)
        self.assertFalse(hasattr(self.e, 'current_mode'))
        self.e.game.right_click.assert_not_called()

    def test_recipe_check_still_blocks_continuation_after_unknown_mode_recovery(self):
        self.e.check_options.side_effect = RuntimeError('配方或复选框状态不同')
        with self.assertRaisesRegex(RuntimeError, '配方'):
            self.e.mode('corrode')
        self.assertEqual(self.e.game.click.call_count, 2)
        self.assertFalse(hasattr(self.e, 'current_mode'))

    def test_no_menu_target_saves_evidence_without_clicking_old_option_coordinates(self):
        self.e.read_saved.return_value = {'details': []}, 'menu.bmp'
        self.e.read_saved.side_effect = None
        with self.assertRaisesRegex(RuntimeError, '未能在魔方功能下拉菜单'):
            self.e.mode('corrode')
        self.e.game.click.assert_called_once_with([280, 28])
        self.e.save_state.assert_called_once_with('mode-menu-option-unrecognized')
        self.e.check_options.assert_not_called()

    def test_confirm_after_missing_target_uses_already_open_menu(self):
        self.e.read_saved.side_effect = None
        self.e.read_saved.return_value = {'details': []}, 'menu.bmp'
        with self.assertRaises(RuntimeError):
            self.e.mode('corrode')
        self.assertTrue(self.state['open'])
        self.e.read_saved.side_effect = self.read_menu
        self.e.mode('corrode')
        self.assertEqual([c.args[0] for c in self.e.game.click.call_args_list], [[280, 28], [244, 120]])

    def test_confirm_reopens_menu_if_user_closed_it_while_paused(self):
        self.e._mode_menu_pending = True
        self.e.mode('corrode')
        self.assertEqual([c.args[0] for c in self.e.game.click.call_args_list], [[280, 28], [244, 120]])

    def test_duplicate_target_labels_stop_without_selecting_either(self):
        region = menu_region(self.e.config, self.snapshot())
        self.e.read_saved.side_effect = None
        self.e.read_saved.return_value = {'details': [self.line('corrode', region),
                                                      self.line('corrode', region, [244, 160])]}, 'menu.bmp'
        with self.assertRaisesRegex(RuntimeError, '多处'):
            self.e.mode('corrode')
        self.e.game.click.assert_called_once_with([280, 28])
        self.e.save_state.assert_called_once_with('mode-menu-ambiguous')

    def test_failed_selection_waits_for_confirmation_without_repeating_menu_input(self):
        def failed_click(point):
            if point == [280, 28]:
                self.state['open'] = True
            else:
                self.state['open'] = False
        self.e.game.click.side_effect = failed_click
        with self.assertRaisesRegex(RuntimeError, '功能切换未得到确认'):
            self.e.mode('corrode')
        self.assertEqual(self.e.game.click.call_count, 2)
        self.e.check_options.assert_not_called()
        self.e.save_state.assert_called_once_with('mode-switch-unconfirmed')

    def test_f10_while_opening_dropdown_never_selects_any_function(self):
        self.e.game.click.side_effect = UserStopped('F10')
        with self.assertRaises(UserStopped):
            self.e.mode('corrode')
        self.e.game.click.assert_called_once_with([280, 28])
        self.e.read_saved.assert_not_called()

    def test_option_text_must_be_exact_and_have_in_region_coordinates(self):
        details = [
            {'text': '可以通过腐蚀获得', 'words': [{'box': [10, 20, 40, 12]}]},
            {'text': '腐蚀', 'words': [{'box': [10, -20, 28, 12]}]},
            {'text': '腐蚀', 'words': [{'box': [1000, 20, 28, 12]}]},
            {'text': '腐蚀', 'words': []}]
        self.e.read_saved.side_effect = None
        self.e.read_saved.return_value = {'details': details}, 'menu.bmp'
        self.assertIsNone(option(self.e, 'corrode', self.snapshot()))
        self.assertEqual(self.e.read_saved.call_count, 2)
        self.e.game.click.assert_not_called()

    def test_second_ocr_scale_can_find_a_missed_option(self):
        region = menu_region(self.e.config, self.snapshot())
        self.e.read_saved.side_effect = [({'details': []}, 'a.bmp'),
                                        ({'details': [self.line('corrode', region)]}, 'b.bmp')]
        self.assertEqual(option(self.e, 'corrode', self.snapshot()), [244, 120])
        self.assertEqual([c.kwargs['scale'] for c in self.e.read_saved.call_args_list], [3, 4])

    def test_menu_region_excludes_the_current_header_and_stays_in_frame(self):
        frame = self.snapshot()
        x, y, width, height = menu_region(self.e.config, frame)
        self.assertGreaterEqual(y, 36)
        self.assertGreaterEqual(x, 0)
        self.assertLessEqual(x + width, frame.width)
        self.assertLessEqual(y + height, frame.height)


class MenuImageTest(unittest.TestCase):
    def setUp(self):
        self.e = fake_engine()
        self.e.config['points'].update(mode_menu=[131, 15], mode_corrode=[86, 96], mode_synthesize=[87, 44])
        self.e.config['regions'] = {'mode_label': [50, 6, 43, 19]}
        self.frame = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'cube-menu-alchemy.bmp')
        self.e.read_saved = Mock(return_value=({'details': []}, 'menu.bmp'))

    def paste(self, frame, reference, x, y):
        pixels = bytearray(frame.pixels)
        for row in range(reference.height):
            start = ((y + row) * frame.width + x) * 4
            pixels[start:start + reference.width * 4] = reference.pixels[row * reference.width * 4:(row + 1) * reference.width * 4]
        return Frame(frame.width, frame.height, bytes(pixels))

    def test_reported_menu_finds_corrosion_even_when_ocr_returns_no_text(self):
        self.assertEqual(option(self.e, 'corrode', self.frame), [85, 95])
        self.e.read_saved.assert_not_called()

    def test_reported_menu_also_finds_synthesis_without_ocr(self):
        self.assertEqual(option(self.e, 'synthesize', self.frame), [85, 42])
        self.e.read_saved.assert_not_called()

    def test_unknown_function_selects_the_real_image_position(self):
        self.e.game.frame.return_value = self.frame
        select_unknown(self.e, 'corrode')
        self.assertEqual([c.args[0] for c in self.e.game.click.call_args_list], [[131, 15], [85, 95]])
        self.e.read_saved.assert_not_called()
        self.e.save_state.assert_not_called()
        self.assertFalse(self.e._mode_menu_pending)

    def test_retry_with_menu_already_open_selects_without_toggling(self):
        self.e._mode_menu_pending = True
        self.e.game.frame.return_value = self.frame
        select_unknown(self.e, 'synthesize')
        self.e.game.click.assert_called_once_with([85, 42])

    def test_window_position_and_menu_position_are_translated_from_calibration(self):
        background = Frame(320, 360, bytes([25, 25, 25, 255]) * 320 * 360)
        frame = self.paste(background, self.frame, 91, 37)
        for key in ('mode_menu', 'mode_corrode', 'mode_synthesize'):
            x, y = self.e.config['points'][key]
            self.e.config['points'][key] = [x + 91, y + 37]
        self.e.config['regions']['mode_label'] = [141, 43, 43, 19]
        self.assertEqual(option(self.e, 'corrode', frame), [176, 132])

    def test_dim_menu_keeps_both_function_names(self):
        pixels = bytearray(self.frame.pixels)
        for n in range(0, len(pixels), 4):
            pixels[n:n + 3] = bytes(round(value * .8) for value in pixels[n:n + 3])
        frame = Frame(self.frame.width, self.frame.height, bytes(pixels))
        for wanted, expected in (('corrode', [85, 95]), ('synthesize', [85, 42])):
            with self.subTest(wanted=wanted):
                self.assertEqual(option(self.e, wanted, frame), expected)
        self.e.read_saved.assert_not_called()

    def test_changed_gold_background_does_not_hide_the_text(self):
        pixels = bytearray(self.frame.pixels)
        for n in range(0, len(pixels), 4):
            b, g, r = pixels[n:n + 3]
            if b == 0 and r > g * 2:
                pixels[n:n + 3] = bytes([0, 100, 180])
        frame = Frame(self.frame.width, self.frame.height, bytes(pixels))
        self.assertEqual(option(self.e, 'corrode', frame), [85, 95])
        self.e.read_saved.assert_not_called()

    def test_duplicate_menu_images_pause_instead_of_choosing_one(self):
        frame = self.paste(self.frame, asset('cube-menu-corrode'), 69, 165)
        with self.assertRaisesRegex(RuntimeError, '多处'):
            option(self.e, 'corrode', frame)
        self.e.save_state.assert_called_once_with('mode-menu-ambiguous')
        self.e.game.click.assert_not_called()
        self.e.read_saved.assert_not_called()

    def test_identical_text_outside_the_menu_search_is_ignored(self):
        background = Frame(345, 296, bytes([25, 25, 25, 255]) * 345 * 296)
        frame = self.paste(background, self.frame, 0, 0)
        frame = self.paste(frame, asset('cube-menu-corrode'), 230, 86)
        self.assertEqual(option(self.e, 'corrode', frame), [85, 95])

    def test_missing_target_does_not_select_another_function_or_use_the_old_point(self):
        blank = Frame(33, 19, bytes([0, 12, 50, 255]) * 33 * 19)
        frame = self.paste(self.frame, blank, 69, 86)
        self.e.game.frame.return_value = frame
        with self.assertRaisesRegex(RuntimeError, '未能在魔方功能下拉菜单'):
            select_unknown(self.e, 'corrode')
        self.e.game.click.assert_called_once_with([131, 15])
        self.e.save_state.assert_called_once_with('mode-menu-option-unrecognized')

    def test_current_function_header_cannot_supply_a_menu_match(self):
        background = Frame(145, 296, bytes([25, 25, 25, 255]) * 145 * 296)
        frame = self.paste(background, asset('cube-menu-corrode'), 50, 2)
        self.assertIsNone(option(self.e, 'corrode', frame))
        self.e.game.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
