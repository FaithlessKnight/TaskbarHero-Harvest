from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Engine
from rules import classify
from windows_io import Frame
import tempfile


def equipment(slot=0, quality='传奇'):
    return classify(slot, ['次元靴', quality + '级', '防御力', '装饰槽', '需要等级90'])


def fake_engine():
    engine = Engine.__new__(Engine)
    engine.config = {'points': {'commit_corrode': [20, 20], 'commit_synthesize': [30, 30],
                                'inventory_first': [100, 200], 'inventory_last': [340, 280]},
                     'bag_columns': 7, 'bag_rows': 3, 'bag_total_rows': 3,
                     'learned': {'synthesize': {'empty_warehouse': Frame(1, 1, bytes([0, 0, 0, 255])).template()}}}
    engine.game = Mock()
    engine.neutral = Mock()
    engine.note = Mock()
    engine.progress = Mock()
    engine.check_options = Mock()
    engine.ensure_panels = Mock()
    engine.collapse_panels = Mock()
    engine.occupied = Mock(return_value=[])
    engine.save_state = Mock(return_value='state.bmp')
    engine.read_stack_count = Mock(return_value=1)
    engine.read_source_count = Mock(return_value=0)
    engine.baseline = Mock(return_value=[])
    engine.board_points = Mock(return_value=[[10 + index, 10] for index in range(9)])
    engine.settled_board = Mock(side_effect=lambda mode, allowed: (engine.game.frame(), engine.occupied(mode)))
    return engine


class ExecutionTest(unittest.TestCase):
    def test_missing_tooltip_retries_and_never_classifies_background(self):
        engine = fake_engine()
        engine.config['hover_seconds'] = 0.65
        engine.read_frame = Mock(return_value=({'lines': ['获得了普通宝箱'], 'text': '获得了普通宝箱'}, 'retry.bmp'))
        item = engine.read_item(0, [])
        self.assertEqual(item.action, 'keep')
        self.assertTrue(item.reason.startswith('未检测到物品详情'))
        self.assertEqual(engine.game.hover.call_count, 2)
        engine.game.hover.assert_called_with([100, 200], 1.5, retry=True)
        engine.game.click.assert_not_called()
        engine.game.drag.assert_not_called()

    def test_no_tooltip_and_visually_empty_cell_marks_end(self):
        engine = fake_engine()
        engine.baseline_frame = Frame(400, 400, bytes([23, 23, 23, 255]) * 400 * 400)
        engine.game.frame.return_value = engine.baseline_frame
        engine.read_frame = Mock(return_value=({'lines': [], 'text': ''}, 'empty.bmp'))
        engine.local_item = Mock(return_value=None)
        item = engine.read_item(0, [])
        self.assertTrue(item.empty)
        self.assertEqual(item.action, 'keep')

    def test_missing_tooltip_on_colored_icon_is_not_empty(self):
        engine = fake_engine()
        engine.baseline_frame = Frame(400, 400, bytes([30, 60, 110, 255]) * 400 * 400)
        engine.game.frame.return_value = engine.baseline_frame
        engine.read_frame = Mock(return_value=({'lines': [], 'text': ''}, 'icon.bmp'))
        engine.local_item = Mock(return_value=None)
        self.assertFalse(engine.read_item(0, []).empty)

    def test_offscreen_global_slot_is_located_before_drag(self):
        engine = fake_engine()
        expected = equipment(slot=48, quality='稀有')
        expected.view_top = 4
        engine._inventory_pager = Mock()
        engine._inventory_pager.point.return_value = [340, 280]
        engine.read_item = Mock(return_value=expected)
        engine.occupied = Mock(return_value=[0])
        engine.transfer('corrode', [expected])
        engine._inventory_pager.seek.assert_called_once_with(4)
        engine.read_item.assert_called_once_with(48, [])
        engine.game.right_click.assert_called_once_with([340, 280], alt=True)
        engine.game.drag.assert_not_called()
        self.assertEqual(engine.vacated_slots, {48})

    def test_other_panel_cannot_override_hovered_equipment(self):
        engine = fake_engine()
        engine.read_frame = Mock(return_value=({'lines': ['供奉材料'], 'text': '供奉材料'}, 'full.bmp'))
        engine.local_item = Mock(return_value=equipment(quality='普通'))
        item = engine.read_item(0, [])
        self.assertEqual(item.action, 'corrode')
        self.assertEqual(item.quality, '普通')
        engine.local_item.assert_called_once_with(0, 'inventory', [100, 200])

    def test_equipped_rarity_does_not_override_current_item(self):
        engine = fake_engine()
        engine.read_frame = Mock(return_value=({'lines': ['天界级', '防御力1000'], 'text': '天界级 防御力1000'}, 'full.bmp'))
        engine.local_item = Mock(return_value=equipment(quality='稀有'))
        item = engine.read_item(0, [])
        self.assertEqual((item.action, item.quality), ('corrode', '稀有'))

    def test_current_offering_is_stored_without_reading_quantity(self):
        engine = fake_engine()
        engine.read_frame = Mock(return_value=({'lines': ['普通级', '材料'], 'text': '普通级 材料'}, 'full.bmp'))
        engine.local_item = Mock(return_value=classify(0, ['普通级', '供奉材料']))
        item = engine.read_item(0, [])
        self.assertEqual(item.action, 'store')
        self.assertIn('供奉', item.reason)
        self.assertEqual(engine.game.hover.call_count, 1)
        engine.read_stack_count.assert_not_called()

    def test_current_legendary_uses_its_own_required_level_not_icon_or_background(self):
        engine = fake_engine()
        engine.read_frame = Mock(side_effect=[
            ({'lines': ['超凡级', '传奇级'], 'text': '超凡级 传奇级'}, 'full.bmp'),
            ({'lines': ['80'], 'text': '80'}, 'level.bmp')])
        engine.local_item = Mock(return_value=classify(0, ['次元头盔', '传奇级', '防御力1156', '需要等级80']))
        item = engine.read_item(0, [])
        self.assertEqual((item.action, item.quality, item.level), ('synthesize', '传奇', 80))
        self.assertEqual(engine.game.hover.call_count, 1)
        engine.read_frame.assert_not_called()
        engine.save_state.assert_called_once_with('inventory-00')

    def test_full_screen_details_cannot_substitute_for_missing_target(self):
        engine = fake_engine()
        engine.read_frame = Mock(return_value=({'lines': ['普通级', '材料'], 'text': '普通级 材料'}, 'full.bmp'))
        engine.local_item = Mock(return_value=None)
        item = engine.read_item(0, [])
        self.assertEqual(item.action, 'keep')
        self.assertTrue(item.reason.startswith('未检测到物品详情'))
        self.assertEqual(engine.game.hover.call_count, 2)

    def test_retry_can_recover_delayed_tooltip(self):
        engine = fake_engine()
        engine.config['hover_seconds'] = 1.2
        engine.read_frame = Mock(side_effect=[
            ({'lines': ['背包'], 'text': '背包'}, 'initial.bmp'),
            ({'lines': ['矿石', '普通级', '材料'], 'text': '矿石 普通级 材料'}, 'retry.bmp')])
        engine.local_item = Mock(side_effect=[None, classify(0, ['矿石', '普通级', '制作材料'])])
        item = engine.read_item(0, ['背包'])
        self.assertEqual(item.action, 'corrode')
        self.assertEqual(item.image, 'state.bmp')

    def test_scanning_page_without_tooltips_is_an_error(self):
        import tempfile
        engine = fake_engine()
        engine.read_item = Mock(return_value=classify(0, ['获得了普通宝箱']))
        with tempfile.TemporaryDirectory() as folder:
            engine.folder = Path(folder)
            with self.assertRaisesRegex(RuntimeError, '未检测到任何物品详情'):
                engine.scan()
            self.assertTrue((engine.folder / 'inventory.json').exists())
        engine.game.click.assert_not_called()

    def test_synthesis_never_commits_less_than_nine(self):
        engine = fake_engine()
        with self.assertRaisesRegex(RuntimeError, '数量'):
            engine.commit('synthesize', [equipment() for _ in range(8)])
        engine.game.click.assert_not_called()

    def test_unexpected_board_never_commits(self):
        engine = fake_engine()
        engine.occupied = Mock(return_value=[0, 1])
        with self.assertRaisesRegex(RuntimeError, '九宫格状态'):
            engine.commit('corrode', [equipment(quality='普通')])
        engine.game.click.assert_not_called()

    def test_offering_found_in_board_vetoes_commit(self):
        engine = fake_engine()
        engine.occupied = Mock(return_value=[0])
        engine.read_item = Mock(return_value=classify(0, ['普通级', '供奉材料']))
        with self.assertRaisesRegex(RuntimeError, '第 1 格.*预期.*读到'):
            engine.commit('corrode', [equipment(quality='普通')])
        engine.game.click.assert_not_called()
        self.assertIn('供奉材料', engine.note.call_args.args[0])

    def test_matching_backpack_and_cube_item_passes_recheck(self):
        engine = fake_engine()
        engine.read_item = Mock(return_value=equipment(quality='稀有'))
        engine.check_options = Mock()
        engine.verify_board('corrode', [equipment(quality='稀有')])
        engine.check_options.assert_called_once_with('corrode')
        engine.game.click.assert_not_called()

    def test_timeout_never_repeats_commit(self):
        engine = fake_engine()
        engine.occupied = Mock(return_value=[0])
        engine.verify_board = Mock()
        with patch('engine.time.monotonic', side_effect=[0, 0, 31]):
            with self.assertRaisesRegex(RuntimeError, '不会重复点击'):
                engine.commit('corrode', [equipment(quality='普通')])
        engine.game.click.assert_called_once_with([20, 20])

    def test_synthesis_output_is_right_clicked_from_center_to_inventory(self):
        engine = fake_engine()
        engine.verify_board = Mock()
        engine.empty_warehouse = Mock(return_value=[50, 50])
        engine.occupied = Mock(side_effect=[list(range(9)), [4], []])
        engine.game.frame.return_value = Frame(1, 1, bytes([180, 100, 50, 255]))
        with patch('engine.cell_box', return_value=[0, 0, 1, 1]):
            engine.commit('synthesize', [equipment(slot=n) for n in range(9)])
        engine.game.click.assert_called_once_with([30, 30])
        engine.game.right_click.assert_called_once_with([14, 10])
        engine.game.drag.assert_not_called()
        engine.empty_warehouse.assert_not_called()

    def test_repacked_bag_is_rescanned_before_next_drag(self):
        engine = fake_engine()
        engine.read_item = Mock(side_effect=[classify(0, []), equipment(slot=2, quality='稀有')])
        engine.scan = Mock(return_value=[equipment(slot=2, quality='稀有')])
        engine.occupied = Mock(return_value=[0])
        engine.transfer('corrode', [equipment(slot=0, quality='稀有')])
        engine.game.right_click.assert_called_once_with([180, 200], alt=True)
        engine.game.drag.assert_not_called()


class TooltipSourceTest(unittest.TestCase):
    def test_only_nearest_panel_is_read_and_offering_is_protected(self):
        for lines, action in ((['次元之剑', '稀有级', '攻击伤害360'], 'corrode'),
                              (['稀有级', '供奉材料'], 'store'),
                              (['稀有级', '攻击伤害360', 'EQUIPPED'], 'keep')):
            with self.subTest(lines=lines), tempfile.TemporaryDirectory() as folder:
                engine = fake_engine()
                engine.baseline_frame = Frame(400, 300, bytes([80, 80, 80, 255]) * 400 * 300)
                after = Mock(spec=Frame)
                after.tooltip_boxes.return_value = [[120, 100, 140, 180], [0, 0, 110, 290]]
                after.crop.return_value = engine.baseline_frame.crop([120, 100, 140, 180])
                engine.game.frame.return_value = after
                path = str(Path(folder) / 'target.bmp')
                engine.read_saved = Mock(side_effect=[
                    ({'lines': [], 'text': ''}, 'before.bmp'),
                    ({'lines': lines, 'text': '\n'.join(lines)}, path),
                    ({'lines': lines, 'text': '\n'.join(lines)}, path),
                    ({'lines': [], 'text': ''}, 'before.bmp')])
                item = engine.local_item(0, 'inventory', [280, 200])
                self.assertEqual(item.action, action)
                after.crop.assert_called_once_with([120, 100, 140, 180])
                if action == 'corrode':
                    self.assertEqual(item.quality, '稀有')

    def test_second_scale_standalone_rarity_remains_in_header(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = fake_engine()
            engine.baseline_frame = Frame(400, 300, bytes([80, 80, 80, 255]) * 400 * 300)
            after = Mock(spec=Frame)
            after.tooltip_boxes.return_value = [[120, 100, 140, 180]]
            after.crop.return_value = engine.baseline_frame.crop([120, 100, 140, 180])
            engine.game.frame.return_value = after
            first = ['次元头盔', '传奇報', '防御力1156', '固有属性', '护甲635', '装饰槽', '空槽', '价格']
            second = ['次元头盔', '传奇', '防御力1156', '固有属性']
            engine.read_saved = Mock(side_effect=[
                ({'lines': [], 'text': ''}, 'before.bmp'),
                ({'lines': first, 'text': '\n'.join(first)}, str(Path(folder) / 'target.bmp')),
                ({'lines': second, 'text': '\n'.join(second)}, 'second.bmp'),
                ({'lines': [], 'text': ''}, 'before.bmp')])
            item = engine.local_item(0, 'inventory', [280, 200])
            self.assertEqual((item.quality, item.kind), ('传奇', 'equipment'))
            self.assertEqual(item.action, 'synthesize')
            self.assertIsNone(item.level)


if __name__ == '__main__':
    unittest.main()
