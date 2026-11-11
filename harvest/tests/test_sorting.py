from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Engine, validate, POINT_LABELS, REGION_LABELS
from inventory import sort_button
from rules import classify, Item
from windows_io import Frame, ROOT
from test_engine import fake_engine, equipment


def low(slot=0):
    return equipment(slot, '稀有')


class SortButtonTest(unittest.TestCase):
    def test_matches_reference_in_backpack_toolbar(self):
        reference = Frame.load_bmp(ROOT / 'assets' / 'inventory-sort.bmp')
        width, height = 350, 200
        pixels = bytearray(bytes([5, 10, 15, 255]) * width * height)
        for y in range(reference.height):
            start = ((146 + y) * width + 86) * 4
            pixels[start:start + reference.width * 4] = reference.pixels[y * reference.width * 4:(y + 1) * reference.width * 4]
        config = {'points': {'inventory_first': [60, 40], 'inventory_last': [300, 120]},
                  'bag_columns': 7, 'bag_rows': 3}
        self.assertEqual(sort_button(Frame(width, height, bytes(pixels)), config), [97, 157])

    def test_absent_button_does_not_guess_a_coordinate(self):
        config = {'points': {'inventory_first': [60, 40], 'inventory_last': [300, 120]},
                  'bag_columns': 7, 'bag_rows': 3}
        with self.assertRaisesRegex(RuntimeError, '无法唯一识别'):
            sort_button(Frame(350, 200, bytes([5, 10, 15, 255]) * 350 * 200), config)

    def test_manual_point_is_optional_for_existing_calibration(self):
        config = {'points': {key: [10, 10] for key in POINT_LABELS if key != 'inventory_sort'},
                  'regions': {key: [10, 10, 10, 10] for key in REGION_LABELS},
                  'learned': {'corrode': {}, 'synthesize': {}}, 'window_size': [500, 500],
                  'bag_columns': 7, 'bag_rows': 3, 'bag_total_rows': 15,
                  'warehouse_columns': 7, 'warehouse_rows': 7}
        config['points'].update(inventory_last=[250, 90], cube_last=[90, 90], warehouse_last=[250, 250])
        validate(config, execution=True)
        config['points']['inventory_sort'] = [100, 140]
        self.assertEqual(sort_button(None, config), [100, 140])


class SortLifecycleTest(unittest.TestCase):
    def setup_engine(self):
        engine = fake_engine()
        engine.occupied = Mock(return_value=[])
        engine._inventory_pager = Mock()
        engine._inventory_pager.snapshot.return_value = [Frame(10, 10, bytes([100, 100, 100, 255]) * 100)]
        engine.vacated_slots = {1, 5}
        engine._stack_cache = {'old': 5}
        engine.baseline_frame = Frame(1, 1, bytes([0, 0, 0, 255]))
        return engine

    def test_only_empty_cube_can_be_followed_by_sort(self):
        engine = self.setup_engine()
        engine.occupied.return_value = [0]
        with self.assertRaisesRegex(RuntimeError, '九宫格尚未清空'):
            engine.sort_inventory()
        engine.game.click.assert_not_called()

    def test_sort_waits_for_stability_then_discards_old_positions_and_caches(self):
        engine = self.setup_engine()
        with patch('engine.sort_button', return_value=[422, 597]):
            engine.sort_inventory()
        engine.game.click.assert_called_once_with([422, 597])
        self.assertEqual(engine.vacated_slots, set())
        self.assertEqual(engine._stack_cache, {})
        self.assertIsNone(engine.baseline_frame)
        engine._inventory_pager.reset.assert_called_once()

    def test_failed_sort_with_remaining_holes_stops(self):
        engine = self.setup_engine()
        empty = Frame(10, 10, bytes([20, 20, 20, 255]) * 100)
        icon = Frame(10, 10, bytes([100, 100, 100, 255]) * 100)
        engine._inventory_pager.snapshot.return_value = [icon, empty, icon]
        with patch('engine.sort_button', return_value=[422, 597]):
            with self.assertRaisesRegex(RuntimeError, '仍有空格夹在物品中间'):
                engine.sort_inventory()


class LoopTest(unittest.TestCase):
    def engine(self, scans):
        engine = fake_engine()
        engine.config['max_batches'] = 1
        engine.mode, engine.transfer, engine.commit, engine.sort_inventory = Mock(), Mock(), Mock(), Mock()
        engine.scan = Mock(side_effect=scans)
        return engine

    def test_new_items_and_relocated_items_are_processed_until_none_remain(self):
        first, moved = low(15), low(0)
        new = classify(1, ['新掉落矿石', '不朽级', '制作材料'])
        engine = self.engine([[first], [moved, new], []])
        order = Mock()
        for name in ('scan', 'transfer', 'commit', 'sort_inventory'):
            order.attach_mock(getattr(engine, name), name)
        with patch('engine.sort_button', return_value=[422, 597]):
            engine.execute([first])
        self.assertEqual(engine.commit.call_count, 2)  # Corrosion ignores the synthesis limit.
        self.assertEqual([i.slot for i in engine.transfer.call_args_list[1].args[1]], [0, 1])
        self.assertEqual([c[0] for c in order.mock_calls], [
            'scan', 'transfer', 'commit', 'sort_inventory',
            'scan', 'transfer', 'commit', 'sort_inventory', 'scan'])
        engine.mode.assert_called_once_with('corrode')

    def test_no_eligible_items_stops_without_sort_or_consumption(self):
        locked = classify(0, ['普通级', '供奉材料', '已锁定'])
        engine = self.engine([[locked]])
        with patch('engine.sort_button', return_value=[422, 597]):
            engine.execute([])
        engine.commit.assert_not_called()
        engine.sort_inventory.assert_not_called()

    def test_failed_commit_does_not_sort_or_start_a_new_round(self):
        engine = self.engine([[low()]])
        engine.commit.side_effect = RuntimeError('数量未减少')
        with patch('engine.sort_button', return_value=[422, 597]):
            with self.assertRaisesRegex(RuntimeError, '数量未减少'):
                engine.execute([low()])
        engine.sort_inventory.assert_not_called()
        engine.scan.assert_called_once()

    def test_sort_detection_is_validated_before_first_consumption(self):
        engine = self.engine([[low()]])
        with patch('engine.sort_button', side_effect=RuntimeError('无法唯一识别')):
            with self.assertRaisesRegex(RuntimeError, '无法唯一识别'):
                engine.execute([low()])
        engine.commit.assert_not_called()

    def test_synthesis_uses_remaining_items_after_finished_corrosion(self):
        approved = [equipment(slot=i) for i in range(9)]
        engine = self.engine([[low()], approved])
        with patch('engine.sort_button', return_value=[422, 597]):
            engine.execute([low()])
        self.assertEqual([c.args[0] for c in engine.commit.call_args_list], ['corrode', 'synthesize'])
        self.assertEqual(engine.sort_inventory.call_count, 2)
        engine.sort_inventory.assert_called_with('synthesize')


if __name__ == '__main__':
    unittest.main()
