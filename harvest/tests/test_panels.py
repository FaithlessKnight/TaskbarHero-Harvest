from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from panels import ensure_panels, button, visible, toggle_button, toggle_mask, collapse_panels, wait_button
from vision import asset
from windows_io import Frame, UserStopped


def inventory_fixture():
    return Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'inventory-open-materials.bmp')


def fill_region(frame, box, color):
    pixels = bytearray(frame.pixels)
    x, y, width, height = box
    for row in range(y, y + height):
        offset = (row * frame.width + x) * 4
        pixels[offset:offset + width * 4] = bytes([*color, 255]) * width
    return Frame(frame.width, frame.height, bytes(pixels))


def highlighted_toggle(frame):
    mask = toggle_mask(frame)
    pixels = bytearray(frame.pixels)
    for n in range(0, len(pixels), 4):
        if mask.pixels[n] == 255:
            pixels[n:n + 4] = bytes([30, 220, 255, 255])
    return Frame(frame.width, frame.height, bytes(pixels))


def panel_frame(opened=(), buttons=True, toggle=None, alternate=False):
    width, height = 360, 300
    pixels = bytearray(bytes([20, 20, 20, 255]) * width * height)
    def place(reference, x, y):
        for row in range(reference.height):
            n = ((y + row) * width + x) * 4
            pixels[n:n + reference.width * 4] = reference.pixels[row * reference.width * 4:(row + 1) * reference.width * 4]
    for index, name in enumerate(('cube', 'warehouse', 'inventory')):
        if name in opened:
            place(asset('panel-' + name), 5 + index * 115, 5)
            if name == 'inventory':
                place(asset('panel-inventory-tab'), 8 + index * 115, 136)
        if buttons:
            reference = ('open-' + name + '-reference') if alternate and name != 'inventory' else 'open-' + name
            place(asset(reference), 5 + index * 115, 220)
    if toggle:
        place(asset(toggle + '-panels'), 332, 270)
    return Frame(width, height, bytes(pixels))


class OpenPanelsTest(unittest.TestCase):
    def engine(self, opened):
        engine = Mock()
        engine.save_state.return_value = 'state.bmp'
        state = set(opened)
        engine.game.frame.side_effect = lambda: panel_frame(state)
        def click(point):
            frame = panel_frame(state)
            choices = [name for name in ('cube', 'warehouse', 'inventory') if button(frame, name) == point]
            if len(choices) != 1:
                raise AssertionError('Unrecognized panel button')
            state.symmetric_difference_update(choices)
        engine.game.click.side_effect = click
        return engine, state

    def test_open_pages_are_not_toggled_closed(self):
        engine, state = self.engine(('cube', 'warehouse', 'inventory'))
        ensure_panels(engine)
        engine.game.click.assert_not_called()
        engine.neutral.assert_not_called()
        self.assertEqual(state, {'cube', 'warehouse', 'inventory'})

    def test_missing_pages_open_in_order_and_each_click_is_verified(self):
        engine, state = self.engine(())
        ensure_panels(engine)
        self.assertEqual(state, {'cube', 'warehouse', 'inventory'})
        self.assertEqual(engine.game.click.call_count, 3)

    def test_only_the_missing_page_is_clicked(self):
        for name in ('cube', 'warehouse', 'inventory'):
            with self.subTest(name=name):
                engine, state = self.engine({'cube', 'warehouse', 'inventory'} - {name})
                ensure_panels(engine)
                engine.game.click.assert_called_once()
                self.assertEqual(state, {'cube', 'warehouse', 'inventory'})

    def test_failed_open_saves_state_without_toggling_or_clicking_other_pages(self):
        engine, state = self.engine(())
        engine.game.click.side_effect = None
        with self.assertRaisesRegex(RuntimeError, '魔方页面打开未确认'):
            ensure_panels(engine)
        engine.game.click.assert_called_once()
        engine.save_state.assert_called_once_with('panel-cube-unconfirmed')

    def test_missing_button_causes_no_guess_or_click(self):
        engine = Mock()
        engine.game.frame.return_value = panel_frame(buttons=False)
        with self.assertRaisesRegex(RuntimeError, '无法唯一识别'):
            ensure_panels(engine)
        engine.game.click.assert_not_called()
        engine.save_state.assert_called_once_with('panel-cube-button-unrecognized')

    def test_f10_during_open_propagates(self):
        engine, state = self.engine(())
        engine.game.click.side_effect = UserStopped('F10')
        with self.assertRaises(UserStopped):
            ensure_panels(engine)
        engine.game.click.assert_called_once()

    def test_open_button_alone_is_not_evidence_that_page_is_visible(self):
        frame = panel_frame()
        for name in ('cube', 'warehouse', 'inventory'):
            self.assertFalse(visible(frame, name))
            self.assertTrue(button(frame, name))

    def test_changed_tab_background_uses_exact_bag_label_not_sorting_icon(self):
        engine = Mock()
        engine.config = {'points': {'inventory_first': [100, 100], 'inventory_last': [340, 180]},
                         'bag_columns': 7, 'bag_rows': 3}
        engine.read_saved.return_value = ({'details': [{'text': '背 包', 'words': [
            {'text': '背', 'box': [30, 10, 11, 12]}, {'text': '包', 'box': [43, 10, 11, 12]}]}]}, 'tab.bmp')
        frame = Frame(400, 300, bytes([20, 20, 20, 255]) * 400 * 300)
        self.assertEqual(button(frame, 'inventory', engine), [112, 214])
        engine.read_saved.assert_called_once()

    def test_wrong_tab_text_never_clicks_expected_position_blindly(self):
        engine = Mock()
        engine.config = {'points': {'inventory_first': [100, 100], 'inventory_last': [340, 180]},
                         'bag_columns': 7, 'bag_rows': 3}
        engine.read_saved.return_value = ({'details': [{'text': '编队', 'words': [
            {'text': '编队', 'box': [30, 10, 24, 12]}]}]}, 'tab.bmp')
        frame = Frame(400, 300, bytes([20, 20, 20, 255]) * 400 * 300)
        with self.assertRaisesRegex(RuntimeError, '无法唯一识别背包页面按钮'):
            button(frame, 'inventory', engine)

    def test_actual_open_bag_that_failed_in_1_14_is_recognized(self):
        self.assertTrue(visible(inventory_fixture(), 'inventory'))

    def test_inventory_contents_do_not_affect_page_visibility(self):
        frame = inventory_fixture()
        # The old reference included x=15,16 in this crop: the leftmost item
        # pixels. Change every icon, including those pixels and the stacks.
        for color in ((0, 0, 0), (23, 23, 23), (210, 20, 130), (80, 220, 30)):
            with self.subTest(color=color):
                changed = fill_region(frame, [15, 12, 270, 112], color)
                self.assertTrue(visible(changed, 'inventory'))

    def test_real_tab_without_the_grid_border_is_not_an_open_bag(self):
        changed = fill_region(inventory_fixture(), [5, 3, 10, 126], (0, 0, 0))
        self.assertFalse(visible(changed, 'inventory'))

    def test_real_grid_border_without_the_selected_tab_is_not_an_open_bag(self):
        changed = fill_region(inventory_fixture(), [8, 134, 94, 31], (0, 0, 0))
        self.assertFalse(visible(changed, 'inventory'))

    def test_unrelated_border_and_tab_do_not_confirm_an_open_bag(self):
        frame = panel_frame(('inventory',))
        # Move only the tab sideways; a search would still find both parts,
        # but they no longer describe a single inventory panel.
        changed = fill_region(frame, [238, 136, 94, 31], (20, 20, 20))
        pixels = bytearray(changed.pixels)
        tab = asset('panel-inventory-tab')
        for row in range(tab.height):
            offset = ((136 + row) * frame.width + 120) * 4
            pixels[offset:offset + tab.width * 4] = tab.pixels[row * tab.width * 4:(row + 1) * tab.width * 4]
        self.assertFalse(visible(Frame(frame.width, frame.height, bytes(pixels)), 'inventory'))

    def test_cube_button_with_real_hover_label_keeps_its_click_center(self):
        frame = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'cube-button-hover.bmp')
        self.assertEqual(button(frame, 'cube'), [frame.width // 2, frame.height // 2])

    def test_startup_with_hover_label_does_not_toggle_already_open_pages(self):
        engine, unused = self.engine(('cube', 'warehouse', 'inventory'))
        frame = panel_frame(('cube', 'warehouse', 'inventory'))
        hovered = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'cube-button-hover.bmp')
        pixels = bytearray(frame.pixels)
        for row in range(hovered.height):
            offset = ((220 + row) * frame.width + 5) * 4
            pixels[offset:offset + hovered.width * 4] = hovered.pixels[row * hovered.width * 4:(row + 1) * hovered.width * 4]
        engine.game.frame.side_effect = None
        engine.game.frame.return_value = Frame(frame.width, frame.height, bytes(pixels))
        ensure_panels(engine)
        engine.game.click.assert_not_called()

    def test_user_cube_and_dim_warehouse_buttons_are_recognized(self):
        for panel in ('cube', 'warehouse'):
            reference = asset('open-' + panel + '-reference')
            self.assertEqual(button(reference, panel), [27, 29])

    def test_missing_warehouse_uses_user_variant_and_confirms_open(self):
        engine = Mock()
        state = {'cube', 'inventory'}
        engine.game.frame.side_effect = lambda: panel_frame(state, alternate=True)
        point = button(panel_frame(state, alternate=True), 'warehouse')
        engine.game.click.side_effect = lambda selected: state.add('warehouse') if selected == point else self.fail('Wrong button')
        ensure_panels(engine)
        engine.game.click.assert_called_once_with(point)
        self.assertEqual(state, {'cube', 'warehouse', 'inventory'})

    def test_already_open_pages_do_not_require_the_cube_toolbar_button(self):
        engine = Mock()
        engine.game.frame.return_value = panel_frame(('cube', 'warehouse', 'inventory'), buttons=False)
        ensure_panels(engine)
        engine.game.click.assert_not_called()
        engine.game.move.assert_not_called()
        engine.save_state.assert_not_called()

    def test_missing_cube_uses_reported_button_with_changed_background(self):
        engine = Mock()
        opened = {'warehouse', 'inventory'}
        source = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'cube-button-1.24-reported.bmp')
        pixels = bytearray(source.pixels)
        for n in range(0, len(pixels), 4):
            x, y = n // 4 % source.width, n // 4 // source.width
            if x < 13 or x > 41 or y < 15 or y > 47:
                pixels[n:n + 3] = bytes([120, 50, 90])
        changed = Frame(source.width, source.height, bytes(pixels))

        def snapshot():
            frame = panel_frame(opened)
            output = bytearray(frame.pixels)
            for y in range(58):
                start = ((220 + y) * frame.width + 5) * 4
                output[start:start + 56 * 4] = bytes([20, 20, 20, 255]) * 56
            for y in range(changed.height):
                start = ((220 + y) * frame.width + 5) * 4
                output[start:start + changed.width * 4] = changed.pixels[y * changed.width * 4:(y + 1) * changed.width * 4]
            return Frame(frame.width, frame.height, bytes(output))

        engine.game.frame.side_effect = snapshot
        engine.game.click.side_effect = lambda point: opened.add('cube') if point == [32, 250] else self.fail('Wrong cube target')
        ensure_panels(engine)
        engine.game.click.assert_called_once_with([32, 250])
        self.assertEqual(opened, {'cube', 'warehouse', 'inventory'})

    def test_reported_cube_button_dim_and_hovered_symbol_are_recognized(self):
        source = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'cube-button-1.24-reported.bmp')
        self.assertEqual(button(source, 'cube'), [27, 29])
        dim = bytearray(source.pixels)
        for n in range(0, len(dim), 4):
            dim[n:n + 3] = bytes(round(channel * 0.7) for channel in dim[n:n + 3])
        for frame in (Frame(source.width, source.height, bytes(dim)), highlighted_toggle(source)):
            self.assertEqual(button(frame, 'cube'), [27, 30])

    def test_warehouse_symbol_is_not_a_cube_button(self):
        with self.assertRaisesRegex(RuntimeError, '魔方页面按钮'):
            button(asset('open-warehouse-reference'), 'cube')

    def test_calibrated_toolbar_region_excludes_an_identical_icon_elsewhere(self):
        frame = Frame(400, 330, bytes([20, 20, 20, 255]) * 400 * 330)
        source = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'cube-button-1.24-reported.bmp')
        output = bytearray(frame.pixels)
        for x, y in ((20, 20), (200, 220)):
            for row in range(source.height):
                start = ((y + row) * frame.width + x) * 4
                output[start:start + source.width * 4] = source.pixels[row * source.width * 4:(row + 1) * source.width * 4]
        frame = Frame(frame.width, frame.height, bytes(output))
        engine = Mock()
        engine.config = {'points': {'inventory_first': [100, 80], 'inventory_last': [340, 160]},
                         'bag_columns': 7, 'bag_rows': 3}
        point = button(frame, 'cube', engine)
        self.assertLessEqual(abs(point[0] - 227), 2)
        self.assertLessEqual(abs(point[1] - 249), 3)
        with self.assertRaisesRegex(RuntimeError, '找到 2 处'):
            button(frame, 'cube')

    def test_button_after_expansion_can_appear_on_a_later_frame(self):
        engine = Mock()
        engine.game.frame.side_effect = [panel_frame(buttons=False), panel_frame()]
        self.assertEqual(wait_button(engine, 'cube'), button(panel_frame(), 'cube'))
        engine.game.wait.assert_called_once_with(0.25)
        engine.game.click.assert_not_called()
        engine.save_state.assert_not_called()

    def test_f10_interrupts_waiting_for_the_toolbar_without_clicking(self):
        engine = Mock()
        engine.game.frame.return_value = panel_frame(buttons=False)
        engine.game.wait.side_effect = UserStopped('F10')
        with self.assertRaises(UserStopped):
            wait_button(engine, 'cube')
        engine.game.frame.assert_called_once()
        engine.game.click.assert_not_called()


class PanelToggleTest(unittest.TestCase):
    def engine(self, opened=(), *, collapsed=False, opened_after_expand=('inventory',)):
        engine = Mock()
        engine.save_state.return_value = 'toggle.bmp'
        state = {'opened': set(opened), 'collapsed': collapsed}

        def frame():
            return panel_frame(state['opened'], buttons=not state['collapsed'],
                               toggle='expand' if state['collapsed'] else 'collapse')

        def click(point):
            image = frame()
            action = 'expand' if state['collapsed'] else 'collapse'
            if point == toggle_button(image, action):
                state['collapsed'] = not state['collapsed']
                state['opened'] = set() if state['collapsed'] else set(opened_after_expand)
            else:
                options = [panel for panel in ('cube', 'warehouse', 'inventory') if button(image, panel) == point]
                self.assertEqual(len(options), 1)
                state['opened'].symmetric_difference_update(options)

        engine.game.frame.side_effect, engine.game.click.side_effect = frame, click
        return engine, state

    def test_all_hidden_expands_first_then_opens_only_missing_pages(self):
        engine, state = self.engine(collapsed=True)
        expanded = toggle_button(engine.game.frame(), 'expand')
        ensure_panels(engine)
        self.assertEqual(state, {'opened': {'cube', 'warehouse', 'inventory'}, 'collapsed': False})
        self.assertEqual(engine.game.click.call_args_list[0].args, (expanded,))
        self.assertEqual(engine.game.click.call_count, 3)  # Expand, cube, warehouse.

    def test_expand_restoring_all_pages_does_not_toggle_them_closed(self):
        engine, state = self.engine(collapsed=True, opened_after_expand=('cube', 'warehouse', 'inventory'))
        ensure_panels(engine)
        engine.game.click.assert_called_once()
        self.assertFalse(state['collapsed'])

    def test_expand_unconfirmed_does_not_repeat_toggle_or_open_other_pages(self):
        engine, state = self.engine(collapsed=True)
        engine.game.click.side_effect = None
        with self.assertRaisesRegex(RuntimeError, '全部页面展开未确认'):
            ensure_panels(engine)
        engine.game.click.assert_called_once()
        engine.game.move.assert_not_called()
        engine.save_state.assert_called_once_with('panels-expand-unconfirmed')

    def test_user_expand_and_hovered_collapse_icons_are_distinguished(self):
        for name, action, expected in (('expand-panels-reference', 'expand', [14, 13]),
                                       ('collapse-panels-reference', 'collapse', [16, 13])):
            frame = asset(name)
            self.assertEqual(toggle_button(frame, action), expected)
            opposite = 'collapse' if action == 'expand' else 'expand'
            self.assertIsNone(toggle_button(frame, opposite, required=False))

    def test_hover_brightness_preserves_arrow_direction(self):
        for name, action in (('expand-panels-reference', 'expand'), ('collapse-panels-reference', 'collapse')):
            source = asset(name)
            frame = highlighted_toggle(source)
            self.assertNotEqual(frame.pixels, source.pixels)
            point = toggle_button(frame, action)
            self.assertLessEqual(abs(point[0] - source.width // 2), 2)
            self.assertLessEqual(abs(point[1] - source.height // 2), 2)
            opposite = 'collapse' if action == 'expand' else 'expand'
            self.assertIsNone(toggle_button(frame, opposite, required=False))

    def test_collapse_confirms_highlighted_expand_arrow_under_stationary_cursor(self):
        engine, state = self.engine(('cube', 'warehouse', 'inventory'))
        normal = engine.game.frame.side_effect
        engine.game.frame.side_effect = lambda: highlighted_toggle(normal()) if state['collapsed'] else normal()
        collapse_panels(engine)
        self.assertTrue(state['collapsed'])
        engine.game.click.assert_called_once()
        engine.game.move.assert_not_called()

    def test_two_expand_buttons_are_ambiguous_and_never_clicked(self):
        frame = panel_frame(buttons=False, toggle='expand')
        reference = asset('expand-panels')
        pixels = bytearray(frame.pixels)
        for y in range(reference.height):
            start = ((20 + y) * frame.width + 20) * 4
            pixels[start:start + reference.width * 4] = reference.pixels[y * reference.width * 4:(y + 1) * reference.width * 4]
        engine = Mock()
        engine.game.frame.return_value = Frame(frame.width, frame.height, bytes(pixels))
        with self.assertRaisesRegex(RuntimeError, '无法唯一识别展开全部页面'):
            ensure_panels(engine)
        engine.game.click.assert_not_called()

    def test_completed_instance_collapses_and_checks_before_returning(self):
        engine, state = self.engine(('cube', 'warehouse', 'inventory'))
        point = toggle_button(engine.game.frame(), 'collapse')
        collapse_panels(engine)
        self.assertEqual(state, {'opened': set(), 'collapsed': True})
        engine.game.click.assert_called_once_with(point)
        engine.game.move.assert_not_called()
        engine.neutral.assert_called_once()
        self.assertIn('已确认全部页面收起', engine.note.call_args.args[0])

    def test_resuming_after_collapse_does_not_reopen_pages(self):
        engine, state = self.engine(collapsed=True)
        collapse_panels(engine)
        engine.game.click.assert_not_called()
        self.assertTrue(state['collapsed'])

    def test_collapse_unconfirmed_pauses_without_clicking_again(self):
        engine, state = self.engine(('cube', 'warehouse', 'inventory'))
        engine.game.click.side_effect = None
        with self.assertRaisesRegex(RuntimeError, '全部页面收起未确认'):
            collapse_panels(engine)
        engine.game.click.assert_called_once()
        engine.save_state.assert_called_once_with('panels-collapse-unconfirmed')

    def test_expand_marker_without_panels_closing_does_not_confirm_collapse(self):
        engine, state = self.engine(('cube', 'warehouse', 'inventory'))
        engine.game.click.side_effect = lambda point: state.update(collapsed=True)
        with self.assertRaisesRegex(RuntimeError, '全部页面收起未确认'):
            collapse_panels(engine)
        engine.game.click.assert_called_once()

    def test_missing_collapse_button_does_not_guess(self):
        engine = Mock()
        engine.game.frame.return_value = panel_frame(('cube', 'warehouse', 'inventory'))
        with self.assertRaisesRegex(RuntimeError, '无法唯一识别收起全部页面'):
            collapse_panels(engine)
        engine.game.click.assert_not_called()

    def test_f10_while_expanding_or_collapsing_propagates(self):
        for operation, collapsed in ((ensure_panels, True), (collapse_panels, False)):
            engine, state = self.engine(collapsed=collapsed)
            engine.game.click.side_effect = UserStopped('F10')
            with self.assertRaises(UserStopped):
                operation(engine)
            engine.game.click.assert_called_once()


if __name__ == '__main__':
    unittest.main()
