from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Engine
from inventory import InventoryPager, same_cells, shifted_rows
from rules import Item, classify
from windows_io import Frame
from test_engine import fake_engine


def cells(values):
    return [Frame(2, 2, bytes([v, v, v, 255]) * 4) for v in values]


def pager():
    p = InventoryPager(Mock(), {'bag_columns': 2, 'bag_rows': 3, 'bag_total_rows': 15},
                       [[10, 10], [20, 10], [10, 20], [20, 20], [10, 30], [20, 30]],
                       [], Mock(), Mock())
    p.snapshot = Mock()
    p.wheel = Mock()
    return p


class PagerTest(unittest.TestCase):
    def test_infers_one_or_two_rows_from_overlap(self):
        before = cells([20, 40, 60, 80, 100, 120])
        self.assertEqual(shifted_rows(before, cells([60, 80, 100, 120, 140, 160]), 2, 3), 1)
        self.assertEqual(shifted_rows(before, cells([100, 120, 140, 160, 180, 200]), 2, 3), 2)
        self.assertIsNone(shifted_rows(before, cells([30, 50, 70, 90, 110, 130]), 2, 3))

    def test_ambiguous_repeating_rows_are_not_counted(self):
        self.assertIsNone(shifted_rows(cells([40] * 6), cells([40] * 6), 2, 3))

    def test_small_wheel_ticks_continue_until_new_row_aligns(self):
        p = pager()
        before = cells([20, 40, 60, 80, 100, 120])
        p.snapshot.side_effect = [before, before, cells([60, 80, 100, 120, 140, 160])]
        self.assertEqual(p.advance(), 1)
        self.assertEqual(p.top, 1)
        self.assertEqual(p.row_delta, 20)
        self.assertEqual([c.args[0] for c in p.wheel.call_args_list], [-10, -10])

    def test_bottom_capacity_prevents_extra_scroll(self):
        p = pager()
        p.top = 12
        self.assertEqual(p.advance(), 0)
        p.wheel.assert_not_called()

    def test_unrecognized_scroll_aborts_instead_of_skipping(self):
        p = pager()
        p.snapshot.return_value = cells([20, 40, 60, 80, 100, 120])
        with self.assertRaisesRegex(RuntimeError, '无法确认行对齐'):
            p.advance()
        self.assertEqual(p.top, 0)
        self.assertEqual(p.wheel.call_count, 60)

    def test_reset_scrolls_up_and_waits_for_two_stable_frames(self):
        p = pager()
        top = cells([20, 40, 60, 80, 100, 120])
        p.snapshot.return_value = top
        p.top = 9
        p.reset()
        self.assertEqual(p.top, 0)
        self.assertEqual([c.args[0] for c in p.wheel.call_args_list], [1800, 120, 120])

    def test_global_slot_uses_current_view_offset(self):
        p = pager()
        p.top = 4
        self.assertEqual(p.point(8), [10, 10])
        self.assertEqual(p.point(13), [20, 30])
        for slot in (7, 14):
            with self.assertRaisesRegex(RuntimeError, '不在当前可见区域'):
                p.point(slot)


class ScanTest(unittest.TestCase):
    def engine(self):
        e = fake_engine()
        e.config['bag_total_rows'] = 15
        p = Mock()
        p.columns, p.rows, p.total_rows, p.top = 7, 3, 15, 0
        def advance():
            p.top += 1
            return 1
        p.advance.side_effect = advance
        e.pager = Mock(return_value=p)
        return e, p

    def item(self, slot, empty=False):
        if empty:
            return Item(slot, 'keep', '', '', None, '已确认空格，停止向后扫描', '', empty=True)
        return classify(slot, ['矿石', '普通级', '制作材料'])

    def test_105_slots_read_once_without_duplicate_overlap(self):
        e, p = self.engine()
        e.read_item = Mock(side_effect=lambda slot, baseline: self.item(slot))
        with tempfile.TemporaryDirectory() as folder:
            e.folder = Path(folder)
            result = e.scan()
        self.assertEqual([i.slot for i in result], list(range(105)))
        self.assertEqual([c.args[0] for c in e.read_item.call_args_list], list(range(105)))
        self.assertEqual(p.advance.call_count, 12)
        self.assertEqual(result[21].view_top, 1)
        self.assertEqual(result[-1].view_top, 12)

    def test_first_confirmed_empty_stops_before_next_cell_or_scroll(self):
        e, p = self.engine()
        e.read_item = Mock(side_effect=lambda slot, baseline: self.item(slot, slot == 20))
        with tempfile.TemporaryDirectory() as folder:
            e.folder = Path(folder)
            result = e.scan()
        self.assertEqual(len(result), 21)
        self.assertTrue(result[-1].empty)
        p.advance.assert_not_called()

    def test_full_third_row_triggers_scroll_and_stops_on_next_row_empty(self):
        e, p = self.engine()
        e.read_item = Mock(side_effect=lambda slot, baseline: self.item(slot, slot == 23))
        with tempfile.TemporaryDirectory() as folder:
            e.folder = Path(folder)
            result = e.scan()
        self.assertEqual([i.slot for i in result], list(range(24)))
        p.advance.assert_called_once()

    def test_no_tooltip_on_occupied_slot_is_not_end_of_inventory(self):
        e, p = self.engine()
        def read(slot, baseline):
            if slot == 0:
                return Item(slot, 'keep', '', '', None, '未检测到物品详情', '')
            return self.item(slot, slot == 2)
        e.read_item = Mock(side_effect=read)
        with tempfile.TemporaryDirectory() as folder:
            e.folder = Path(folder)
            result = e.scan()
        self.assertEqual(len(result), 3)
        self.assertEqual(result[1].action, 'corrode')

    def test_own_drag_holes_do_not_hide_remaining_approved_items(self):
        e, p = self.engine()
        e.vacated_slots = {0}
        e.read_item = Mock(side_effect=lambda slot, baseline: self.item(slot, slot in (0, 3)))
        with tempfile.TemporaryDirectory() as folder:
            e.folder = Path(folder)
            result = e.scan()
        self.assertEqual([i.slot for i in result], [1, 2, 3])

    def test_empty_inventory_is_valid_and_creates_no_execution_plan(self):
        e, p = self.engine()
        e.read_item = Mock(side_effect=lambda slot, baseline: self.item(slot, True))
        with tempfile.TemporaryDirectory() as folder:
            e.folder = Path(folder)
            result = e.scan()
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].empty)
        p.advance.assert_not_called()


if __name__ == '__main__':
    unittest.main()
