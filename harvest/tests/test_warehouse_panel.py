"""Warehouse opening after the hero panel moves away from saved coordinates."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from panels import button, ensure_panels, toolbar_region
from vision import asset
from windows_io import Frame, UserStopped


FIXTURES = Path(__file__).parent / 'fixtures'


def place(frame, reference, x, y):
    pixels = bytearray(frame.pixels)
    for row in range(reference.height):
        start = ((y + row) * frame.width + x) * 4
        pixels[start:start + reference.width * 4] = reference.pixels[row * reference.width * 4:(row + 1) * reference.width * 4]
    return Frame(frame.width, frame.height, bytes(pixels))


class WarehousePanelTest(unittest.TestCase):
    def setUp(self):
        self.source = Frame.load_bmp(FIXTURES / 'warehouse-shifted-toolbar.bmp')
        self.e = Mock()
        self.e.config = {'points': {'inventory_first': [240, 90], 'inventory_last': [479, 165]},
                         'bag_columns': 7, 'bag_rows': 3}
        self.e.save_state.return_value = 'state.bmp'

    def snapshot(self, opened=False, offset=(70, 60)):
        frame = Frame(520, 330, bytes([20, 20, 20, 255]) * 520 * 330)
        frame = place(frame, self.source, *offset)
        frame = place(frame, asset('panel-cube'), 400, 5)
        if opened:
            frame = place(frame, asset('panel-warehouse'), 400, 35)
        return frame

    def test_user_reported_warehouse_button_is_recognized(self):
        frame = Frame.load_bmp(FIXTURES / 'warehouse-button-1.27-reported.bmp')
        self.assertEqual(button(frame, 'warehouse'), [26, 25])

    def test_current_inventory_position_recovers_the_button_outside_old_search(self):
        frame = self.snapshot()
        self.assertEqual(toolbar_region(frame, self.e), [68, 232, 319, 75])
        self.assertEqual(button(frame, 'warehouse', self.e), [111, 267])
        self.assertLess(111, 200)  # The old calibrated search started at x=200.

    def test_toolbar_search_follows_the_current_panel_position(self):
        self.assertEqual(button(self.snapshot(offset=(25, 20)), 'warehouse', self.e), [66, 227])

    def test_identical_icon_outside_current_toolbar_is_ignored(self):
        frame = place(self.snapshot(), asset('open-warehouse-alternate'), 300, 20)
        self.assertEqual(button(frame, 'warehouse', self.e), [111, 267])

    def test_two_icons_inside_current_toolbar_are_ambiguous(self):
        frame = place(self.snapshot(), asset('open-warehouse-alternate'), 350, 250)
        with self.assertRaisesRegex(RuntimeError, '仓库页面按钮（找到 2 处）'):
            button(frame, 'warehouse', self.e)
        self.e.game.click.assert_not_called()

    def test_tab_without_corresponding_grid_does_not_move_the_search(self):
        frame = Frame(520, 330, bytes([20, 20, 20, 255]) * 520 * 330)
        frame = place(frame, asset('panel-inventory-tab'), 81, 201)
        self.assertEqual(toolbar_region(frame, self.e), [200, 197, 319, 103])

    def test_missing_warehouse_clicks_reported_button_and_confirms_page(self):
        state = {'open': False}
        self.e.game.frame.side_effect = lambda: self.snapshot(state['open'])
        def click(point):
            self.assertEqual(point, [111, 267])
            state['open'] = True
        self.e.game.click.side_effect = click
        ensure_panels(self.e)
        self.e.game.click.assert_called_once_with([111, 267])
        self.e.save_state.assert_not_called()
        self.assertTrue(state['open'])
        # Retrying after confirmation must not close the now-open warehouse.
        ensure_panels(self.e)
        self.e.game.click.assert_called_once_with([111, 267])

    def test_open_not_confirmed_pauses_without_repeated_clicks(self):
        self.e.game.frame.return_value = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, '仓库页面打开未确认'):
            ensure_panels(self.e)
        self.e.game.click.assert_called_once_with([111, 267])
        self.e.save_state.assert_called_once_with('panel-warehouse-unconfirmed')

    def test_f10_stops_before_warehouse_is_opened(self):
        self.e.game.frame.return_value = self.snapshot()
        self.e.game.click.side_effect = UserStopped('F10')
        with self.assertRaises(UserStopped):
            ensure_panels(self.e)
        self.e.game.click.assert_called_once_with([111, 267])


if __name__ == '__main__':
    unittest.main()
