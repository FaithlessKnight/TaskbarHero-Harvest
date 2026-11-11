from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Engine
from rules import classify
from vision import asset
from warehouse_pages import Pages, StorageResult, WarehousePager, locate, tab_mask
from windows_io import Frame, UserStopped
from test_engine import fake_engine, equipment
from test_streaming import runner
import test_warehouse as warehouse
from test_warehouse import warehouse_frame, warehouse_stack_frame


FIXTURES = Path(__file__).parent / 'fixtures'
PAGE_CONFIG = {'warehouse_columns': 7, 'warehouse_rows': 7,
               'points': {'warehouse_first': [35, 117], 'warehouse_last': [275, 357]}}


def paste(frame, overlay, x, y):
    pixels = bytearray(frame.pixels)
    for row in range(overlay.height):
        offset = ((y + row) * frame.width + x) * 4
        pixels[offset:offset + overlay.width * 4] = overlay.pixels[row * overlay.width * 4:(row + 1) * overlay.width * 4]
    return Frame(frame.width, frame.height, bytes(pixels))


def solid(width, height, color=(23, 23, 23, 255)):
    return Frame(width, height, bytes(color) * width * height)


def page_frame(selected=1, count=4):
    """Replay the supplied pixels, moving/recoloring only the numeral glyphs."""
    frame = Frame.load_bmp(FIXTURES / 'warehouse-full-page.bmp')
    for n in range(4):
        frame = paste(frame, solid(12, 18), 25 + n * 36, 62)
    frame = paste(frame, solid(26, 20), 160, 59)
    for number in range(1, count + 1):
        mask = tab_mask(asset(f'warehouse-page-{number}'))
        pixels = bytearray(mask.pixels)
        for n in range(0, len(pixels), 4):
            pixels[n:n + 4] = bytes((255, 255, 255, 255) if pixels[n] and number == selected
                                   else (45, 100, 170, 255) if pixels[n] else (23, 23, 23, 255))
        glyph = Frame(mask.width, mask.height, bytes(pixels))
        frame = paste(frame, glyph, 25 + (number - 1) * 36, 62 if number == selected else 66)
    return paste(frame, asset('warehouse-pages-add'), 25 + count * 36 - 9, 59)


class WarehouseTabsTest(unittest.TestCase):
    def test_supplied_full_warehouse_has_four_tabs_and_49_occupied_cells(self):
        frame = Frame.load_bmp(FIXTURES / 'warehouse-full-page.bmp')
        self.assertEqual(locate(PAGE_CONFIG, frame), Pages([[31, 69], [67, 73], [103, 73], [139, 73]], 1))
        e = fake_engine()
        e.config = PAGE_CONFIG
        cells = e.warehouse_cells(frame)
        self.assertEqual(len(cells), 49)
        self.assertTrue(all(not cell.empty_inventory_slot() for cell in cells))

    def test_all_selected_tabs_and_available_page_counts(self):
        for count in range(1, 5):
            for selected in range(1, count + 1):
                with self.subTest(count=count, selected=selected):
                    pages = locate(PAGE_CONFIG, page_frame(selected, count))
                    self.assertEqual(pages.current, selected)
                    self.assertEqual(pages.points, [[31 + 36 * n, 69 if n + 1 == selected else 73]
                                                   for n in range(count)])

    def test_whole_panel_can_move_with_calibration(self):
        frame = paste(solid(355, 479), page_frame(3), 20, 15)
        config = dict(PAGE_CONFIG, points={'warehouse_first': [55, 132], 'warehouse_last': [295, 372]})
        pages = locate(config, frame)
        self.assertEqual(pages, Pages([[51, 88], [87, 88], [123, 84], [159, 88]], 3))

    def test_missing_number_or_duplicate_number_is_not_a_page_list(self):
        frame = page_frame()
        missing = paste(frame, solid(12, 18), 61, 62)
        duplicate = paste(frame, frame.crop([61, 66, 12, 14]), 225, 66)
        self.assertIsNone(locate(PAGE_CONFIG, missing))
        self.assertIsNone(locate(PAGE_CONFIG, duplicate))

    def test_add_button_must_confirm_end_of_unlocked_pages(self):
        frame = paste(page_frame(), solid(26, 20), 160, 59)
        self.assertIsNone(locate(PAGE_CONFIG, frame))
        # An unknown extra page before the add button cannot be silently ignored.
        frame = paste(frame, asset('warehouse-pages-add'), 196, 59)
        self.assertIsNone(locate(PAGE_CONFIG, frame))

    def test_zero_or_two_selected_numerals_is_rejected(self):
        frame = page_frame()
        one = frame.crop([25, 62, 12, 14])
        frame = paste(frame, solid(12, 18), 25, 62)
        none_selected = paste(frame, one, 25, 66)
        self.assertIsNone(locate(PAGE_CONFIG, none_selected))
        both_selected = paste(page_frame(), solid(12, 18), 61, 62)
        both_selected = paste(both_selected, page_frame().crop([61, 66, 12, 14]), 61, 62)
        self.assertIsNone(locate(PAGE_CONFIG, both_selected))


class WarehousePageSelectionTest(unittest.TestCase):
    def pager(self, after, *, before=None):
        e = fake_engine()
        e.config.update(warehouse_columns=7, warehouse_rows=7)
        e.config['points'].update(PAGE_CONFIG['points'])
        e.inventory_point = Mock(return_value=[35, 417])
        before = before or page_frame()
        state = {'clicked': False}
        e.game.frame.side_effect = lambda: after if state['clicked'] else before
        e.game.click.side_effect = lambda point: state.update(clicked=True)
        return WarehousePager(e), e

    def test_confirm_selected_page_and_stable_cells_before_returning(self):
        pager, e = self.pager(page_frame(2))
        pager.select(2, equipment())
        e.game.click.assert_called_once_with([67, 73])
        e.game.right_click.assert_not_called()
        self.assertEqual(pager.pages.current, 2)
        self.assertEqual(e.game.wait.call_count, 2)

    def test_current_page_does_not_click_a_toggle_again(self):
        pager, e = self.pager(page_frame())
        pager.select(1, equipment())
        e.game.click.assert_not_called()
        e.game.wait.assert_not_called()

    def test_number_must_be_in_recognized_tabs_not_add_or_sort(self):
        pager, e = self.pager(page_frame())
        for number in (0, 5):
            with self.assertRaisesRegex(RuntimeError, '页签数量'):
                pager.select(number, equipment())
        e.game.click.assert_not_called()

    def test_click_without_page_change_pauses_without_transfer(self):
        pager, e = self.pager(page_frame())
        with self.assertRaisesRegex(RuntimeError, '翻页未确认'):
            pager.select(2, equipment())
        e.game.click.assert_called_once()
        e.game.right_click.assert_not_called()

    def test_selected_tab_with_unstable_content_does_not_confirm(self):
        after = page_frame(2)
        other = paste(after, solid(32, 32), 19, 101)
        pager, e = self.pager(after)
        e.game.frame.side_effect = [page_frame(), after, other] * 5
        with self.assertRaisesRegex(RuntimeError, '翻页未确认'):
            pager.select(2, equipment())
        e.game.right_click.assert_not_called()

    def test_changed_backpack_or_cube_during_page_switch_never_transfers(self):
        for change in ('source', 'cube'):
            with self.subTest(change=change):
                after = page_frame(2)
                if change == 'source':
                    after = paste(after, solid(32, 32, (220, 80, 150, 255)), 19, 401)
                pager, e = self.pager(after)
                if change == 'cube':
                    e.occupied.side_effect = [[], [0]]
                with self.assertRaisesRegex(RuntimeError, '原物品或魔方槽位变化'):
                    pager.select(2, equipment())
                e.game.right_click.assert_not_called()

    def test_changed_page_count_pauses_before_clicking(self):
        pager, e = self.pager(page_frame())
        e.game.frame.side_effect = None
        e.game.frame.return_value = page_frame(count=3)
        with self.assertRaisesRegex(RuntimeError, '页签数量'):
            pager.select(2, equipment())
        e.game.click.assert_not_called()

    def test_f10_during_page_confirmation_propagates(self):
        pager, e = self.pager(page_frame(2))
        e.game.wait.side_effect = UserStopped('F10')
        with self.assertRaises(UserStopped):
            pager.select(2, equipment())
        e.game.right_click.assert_not_called()


class WarehouseRoutingTest(unittest.TestCase):
    def route(self, current_page, results, expected_pages):
        e = fake_engine()
        item = classify(0, ['至宝级', '防御力100'])
        e.store_current_page = Mock(side_effect=results)
        pager = Mock()
        pager.pages = Pages([[31 + 36 * n, 73] for n in range(4)], current_page)
        with patch('engine.WarehousePager', return_value=pager):
            result = e.store_item(item, recognized=True)
        self.assertEqual([call.args[0] for call in pager.select.call_args_list], expected_pages)
        self.assertEqual(e.store_current_page.call_count, len(expected_pages) + 1)
        return e, result

    def test_full_first_page_uses_next_page(self):
        unused, result = self.route(1, [StorageResult.FULL, True], [2])
        self.assertIs(result, True)

    def test_last_page_full_then_first_page_recheck_can_store(self):
        unused, result = self.route(3, [StorageResult.FULL, StorageResult.FULL, True], [4, 1])
        self.assertIs(result, True)

    def test_a_previously_full_page_can_receive_during_recheck(self):
        unused, result = self.route(1, [StorageResult.FULL] * 5 + [True], [2, 3, 4, 1, 2])
        self.assertIs(result, True)

    def test_all_pages_full_finishes_after_one_recheck(self):
        e, result = self.route(1, [StorageResult.FULL] * 8, [2, 3, 4, 1, 2, 3, 4])
        self.assertIs(result, StorageResult.FULL)
        self.assertFalse(getattr(e, 'vacated_slots', set()))
        e.game.right_click.assert_not_called()

    def test_wrong_target_does_not_try_more_pages(self):
        unused, result = self.route(2, [StorageResult.FULL, False], [3])
        self.assertIs(result, False)

    def test_failed_page_confirmation_prevents_second_shortcut(self):
        e = fake_engine()
        e.store_current_page = Mock(return_value=StorageResult.FULL)
        pager = Mock()
        pager.pages = Pages([[31, 69], [67, 73]], 1)
        pager.select.side_effect = RuntimeError('翻页未确认')
        with patch('engine.WarehousePager', return_value=pager):
            with self.assertRaisesRegex(RuntimeError, '翻页未确认'):
                e.store_item(classify(0, ['天界级', '材料']), recognized=True)
        e.store_current_page.assert_called_once()

    def test_full_page_still_tries_stack_before_any_page_lookup(self):
        before, after = warehouse_stack_frame(full=True), warehouse_stack_frame(after=True, full=True)
        e, clock = warehouse.WarehouseExecutionTest().engine(after, before)
        with patch('engine.time.monotonic', side_effect=lambda: clock['now']), patch('engine.WarehousePager') as pager:
            self.assertIs(e.store_item(e.read_item()), True)
        pager.assert_not_called()
        e.game.right_click.assert_called_once_with([100, 200], ctrl=True)

    def test_confirmed_full_requires_original_source_and_unchanged_board(self):
        before = warehouse_stack_frame(full=True)
        e, clock = warehouse.WarehouseExecutionTest().engine(before, before)
        with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
            self.assertIs(e.store_current_page(e.read_item()), StorageResult.FULL)
        self.assertEqual(clock['now'], 1)
        e.game.right_click.assert_called_once()

    def test_nonfull_no_transfer_or_changed_source_does_not_mean_full(self):
        for before, after in ((warehouse_frame(), warehouse_frame()),
                              (warehouse_stack_frame(full=True),
                               paste(warehouse_stack_frame(full=True), solid(32, 32, (220, 80, 150, 255)), 84, 184))):
            e, clock = warehouse.WarehouseExecutionTest().engine(after, before)
            with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
                with self.assertRaisesRegex(RuntimeError, '不重复点击'):
                    e.store_current_page(e.read_item())
            e.game.right_click.assert_called_once()

    def test_full_skip_does_not_count_store_or_send_to_cube_and_next_item_runs(self):
        r = runner()
        item = classify(0, ['天界级', '供奉材料'])
        r.e.store_item = Mock(return_value=StorageResult.FULL)
        r.accept(item)
        r.accept(replace(item, slot=4))
        r.e.store_item.assert_called_once_with(item, recognized=True)
        self.assertEqual(r.stored, 0)
        self.assertFalse(r.batch)
        r.e.game.right_click.assert_not_called()
        r.accept(equipment(1, '稀有'))
        self.assertEqual(len(r.batch), 1)
        r.e.game.right_click.assert_called_once_with([140, 200], alt=True)

    def test_page_change_alone_is_not_transfer_evidence(self):
        for receive in (False, True):
            with self.subTest(receive=receive):
                full = warehouse_stack_frame(full=True)
                empty = paste(warehouse_frame(), full.crop([84, 184, 32, 32]), 84, 184)
                moved = warehouse_frame(source=False, destination=True)
                e, clock = warehouse.WarehouseExecutionTest().engine(full, full)
                state = {'page': 1, 'moved': False}
                e.game.frame.side_effect = lambda: full if state['page'] == 1 else moved if state['moved'] else empty
                e.game.click.side_effect = lambda point: state.update(page=2)

                def shortcut(*args, **kwargs):
                    if state['page'] == 2 and receive:
                        state['moved'] = True

                e.game.right_click.side_effect = shortcut
                with patch('engine.time.monotonic', side_effect=lambda: clock['now']), \
                        patch('warehouse_pages.locate', side_effect=lambda config, frame: Pages(
                            [[30, 70], [66, 70]], 1 if frame is full else 2)):
                    if receive:
                        self.assertIs(e.store_item(e.read_item()), True)
                        self.assertEqual(e.vacated_slots, {0})
                    else:
                        with self.assertRaisesRegex(RuntimeError, '不重复点击'):
                            e.store_item(e.read_item())
                        self.assertFalse(getattr(e, 'vacated_slots', set()))
                self.assertEqual(e.game.right_click.call_count, 2)
                self.assertTrue(all(call.kwargs == {'ctrl': True} for call in e.game.right_click.call_args_list))
                e.game.click.assert_called_once_with([66, 70])

    def test_legacy_execution_skips_full_storage_and_continues_corrosion(self):
        e = fake_engine()
        high, low = classify(0, ['至宝级', '材料']), equipment(1, '稀有')
        e.scan = Mock(side_effect=[[high, low], [high, low], [high]])
        e.mode, e.transfer, e.commit, e.sort_inventory = [Mock() for n in range(4)]
        e.store_item = Mock(return_value=StorageResult.FULL)
        with patch('engine.sort_button', return_value=[422, 597]):
            e.execute([high, low])
        e.store_item.assert_called_once()
        e.commit.assert_called_once_with('corrode', [low])
        e.sort_inventory.assert_called_once()


if __name__ == '__main__':
    unittest.main()
