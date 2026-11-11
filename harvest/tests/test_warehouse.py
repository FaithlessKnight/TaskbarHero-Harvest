from dataclasses import replace
from itertools import cycle
import unittest
from unittest.mock import Mock, patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Engine
from inventory import same_cells, same_warehouse_cells
from rules import classify, batches, WAREHOUSE_QUALITIES, selected_for_corrosion, has_item_details
from windows_io import Frame, Game
from test_engine import fake_engine, equipment
from test_stacks import digit_frame


class RoutingTest(unittest.TestCase):
    def test_observed_celestial_soulstone_header_is_stored_without_quantity_ocr(self):
        item = classify(0, ['天 界 報', "' 员 魂 石", '召唤折磨难度Boss所需的材料'])
        self.assertTrue(has_item_details(['天 界 報', "' 员 魂 石"]))
        self.assertEqual((item.action, item.quality, item.kind, item.material_type),
                         ('store', '天界', 'material', 'soulstone'))

    def test_storage_header_error_is_only_accepted_as_a_short_header_line(self):
        for header in ('至 宝 鳅', '至宝龌', '天界報', '0 天 界'):
            self.assertEqual(classify(0, [header, '防御力100']).action, 'store')
        for text in ('这件装备属于天界報的掉落', '至宝材料箱', '罕见物品可升级至宝級'):
            self.assertEqual(classify(0, [text]).action, 'keep')
        self.assertEqual(classify(0, ['天界報', '普通级', '材料']).action, 'keep')

    def test_five_qualities_go_to_warehouse_even_when_corrosion_is_selected(self):
        for quality in WAREHOUSE_QUALITIES:
            for label in ('制作材料', '装饰材料', '雕刻材料', '防御力100', '尚未识别类型'):
                item = classify(0, [quality + '级', label], selection={'equipment': ['至宝'], 'material': ['至宝']})
                self.assertEqual(item.action, 'store')
                self.assertFalse(selected_for_corrosion(item, {'equipment': ['至宝'], 'material': ['至宝']}))
                self.assertEqual(batches([item]), [('store', [item])])
            self.assertEqual(classify(0, [quality + '级', '制作材料', '供奉材料']).action, 'store')
            self.assertEqual(classify(0, [quality + '级', '制作材料', '已锁定']).action, 'keep')

    def test_non_target_qualities_and_conflicting_rarity_are_not_stored(self):
        for quality in ('普通', '罕见', '稀有', '传奇', '不朽', '奥秘'):
            self.assertNotEqual(classify(0, [quality + '级', '材料']).action, 'store')
        self.assertEqual(classify(0, ['普通级', '宇宙级', '材料']).action, 'keep')

    def test_shield_block_stat_is_equipment_but_description_alone_is_not(self):
        for line in ('格挡几率17.5%', '格挡鳥率17.5%', '格擋鸟率17%'):
            self.assertEqual(classify(0, ['永恒盾', '罕见级', line]).action, 'corrode')
        self.assertEqual(classify(0, ['罕见级', '可能提高格挡几率17.5%']).action, 'keep')
        self.assertEqual(classify(0, ['罕见级', '装饰材料', '格挡几率17.5%']).kind, 'material')

    def test_warehouse_material_does_not_need_stack_ocr(self):
        engine = fake_engine()
        item = classify(0, ['宇宙级', '制作材料'])
        engine.local_item = Mock(return_value=item)
        engine.read_item(0, [])
        engine.read_stack_count.assert_not_called()

    def test_quantity_fallback_needs_two_matching_reads_and_rejects_conflict(self):
        for texts, expected in ((['33333', '', '333333'], 3),
                                (['33333', '', '', '3333333'], 3),
                                (['33333', '', '222222'], None),
                                (['33333', '22222222'], None),
                                (['33333', '', '', ''], None)):
            engine = fake_engine()
            frame = Mock(spec=Frame)
            frame.crop.return_value = digit_frame()
            engine.read_saved = Mock(side_effect=[({'text': t}, 'glyph.bmp') for t in texts])
            self.assertEqual(Engine.read_stack_count(engine, frame, 'inventory', [100, 200]), expected)


def warehouse_frame(source=True, destination=False):
    width = height = 400
    pixels = bytearray(bytes([23, 23, 23, 255]) * width * height)
    boxes = []
    if source:
        boxes.append([84, 184, 32, 32])
    if destination:
        boxes.append([184, 284, 32, 32])
    for x, y, w, h in boxes:
        for row in range(y, y + h):
            start = (row * width + x) * 4
            pixels[start:start + w * 4] = bytes([180, 90, 160, 255]) * w
    return Frame(width, height, bytes(pixels))


def warehouse_stack_frame(after=False, *, full=False, source_retained=False):
    folder = Path(__file__).parent / 'fixtures'
    frame = warehouse_frame(source=False, destination=False)
    pixels = bytearray(frame.pixels)
    if full:
        for x, y in ((184, 284), (224, 284), (184, 324), (224, 324)):
            for row in range(32):
                offset = ((y + row) * frame.width + x) * 4
                pixels[offset:offset + 32 * 4] = bytes([180, 90, 160, 255]) * 32
    source = Frame.load_bmp(folder / ('warehouse-stack-source-before.bmp' if source_retained
                                    else f'warehouse-stack-source-{"after" if after else "before"}.bmp'))
    stack = Frame.load_bmp(folder / f'warehouse-stack-{"after" if after else "before"}-21.bmp')
    for cell, x, y in ((source, 84, 184), (stack, 184, 284)):
        for row in range(cell.height):
            offset = ((y + row) * frame.width + x) * 4
            pixels[offset:offset + cell.width * 4] = cell.pixels[row * cell.width * 4:(row + 1) * cell.width * 4]
    return Frame(frame.width, frame.height, bytes(pixels))


class WarehouseExecutionTest(unittest.TestCase):
    def engine(self, after, before=None):
        engine = fake_engine()
        engine.config.update(warehouse_columns=2, warehouse_rows=2)
        engine.config['points'].update(warehouse_first=[200, 300], warehouse_last=[240, 340])
        engine.empty_warehouse = Mock(return_value=[200, 300])
        engine.read_item = Mock(return_value=classify(0, ['宇宙级', '未知类型']))
        phase = {'clicked': False}
        engine.game.frame.side_effect = lambda: after if phase['clicked'] else before or warehouse_frame()
        engine.game.right_click.side_effect = lambda *a, **k: phase.update(clicked=True)
        clock = {'now': 0}
        engine.game.wait.side_effect = lambda seconds: clock.update(now=clock['now'] + seconds)
        return engine, clock

    def test_ctrl_click_requires_empty_source_and_three_stable_warehouse_frames(self):
        engine, clock = self.engine(warehouse_frame(source=False, destination=True))
        with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
            engine.store_item(engine.read_item())
        engine.game.right_click.assert_called_once_with([100, 200], ctrl=True)
        self.assertEqual(engine.vacated_slots, {0})
        self.assertEqual(clock['now'], 0.5)
        engine.game.drag.assert_not_called()
        engine.game.click.assert_not_called()

    def test_source_unchanged_or_warehouse_unchanged_never_claims_success_or_reclicks(self):
        for after in (warehouse_frame(source=True, destination=True), warehouse_frame(source=False), warehouse_frame()):
            engine, clock = self.engine(after)
            with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
                with self.assertRaisesRegex(RuntimeError, '不重复点击'):
                    engine.store_item(engine.read_item())
            engine.game.right_click.assert_called_once()

    def test_ctrl_wrong_target_is_returned_and_never_committed_or_retried(self):
        after = warehouse_frame(source=True)
        engine, clock = self.engine(after)
        engine.occupied.side_effect = lambda mode, frame: [0, 2] if frame is after else [0]
        engine.return_to_inventory = Mock()
        with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
            result = engine.store_item(engine.read_item())
        self.assertIs(result, False)
        engine.game.right_click.assert_called_once_with([100, 200], ctrl=True)
        engine.return_to_inventory.assert_called_once_with(slots={2}, allowed={0, 2})
        engine.save_state.assert_called_once_with('warehouse-shortcut-wrong-target')
        engine.game.click.assert_not_called()

    def test_transient_cube_flash_during_storage_is_not_returned_as_an_item(self):
        after = warehouse_frame(source=False, destination=True)
        engine, clock = self.engine(after)
        engine.occupied.side_effect = [[], [0], [], [], []]
        engine.return_to_inventory = Mock()
        with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
            self.assertTrue(engine.store_item(engine.read_item()))
        engine.return_to_inventory.assert_not_called()

    def test_changed_identity_blocks_shortcut(self):
        engine, clock = self.engine(warehouse_frame())
        with self.assertRaisesRegex(RuntimeError, '发生变化'):
            engine.store_item(classify(0, ['天界级', '材料']))
        engine.game.right_click.assert_not_called()

    def test_real_count_two_to_three_is_detected_and_confirms_storage(self):
        before, after = warehouse_stack_frame(), warehouse_stack_frame(after=True)
        engine, clock = self.engine(after, before)
        first, second = engine.warehouse_cells(before), engine.warehouse_cells(after)
        self.assertTrue(same_cells(first, second))  # The original false timeout.
        self.assertFalse(same_warehouse_cells(first, second))
        offering = classify(0, ['被污染的圣物', '稀有级', '供奉材料'])
        with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
            self.assertTrue(engine.store_item(offering, recognized=True))
        engine.game.right_click.assert_called_once_with([100, 200], ctrl=True)
        engine.empty_warehouse.assert_not_called()
        self.assertEqual(engine.vacated_slots, {0})
        self.assertEqual(clock['now'], 0.5)

    def test_full_warehouse_can_receive_into_existing_stack(self):
        before, after = warehouse_stack_frame(full=True), warehouse_stack_frame(after=True, full=True)
        engine, clock = self.engine(after, before)
        engine.empty_warehouse.side_effect = RuntimeError('仓库无空位')
        self.assertTrue(all(not cell.empty_inventory_slot() for cell in engine.warehouse_cells(before)))
        with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
            self.assertTrue(engine.store_item(engine.read_item()))
        engine.empty_warehouse.assert_not_called()
        engine.game.right_click.assert_called_once_with([100, 200], ctrl=True)

    def test_full_warehouse_without_transfer_pauses_after_one_shortcut(self):
        before = warehouse_stack_frame(full=True)
        engine, clock = self.engine(before, before)
        engine.empty_warehouse.side_effect = RuntimeError('仓库无空位')
        with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
            with self.assertRaisesRegex(RuntimeError, '不重复点击'):
                engine.store_item(engine.read_item())
        engine.game.right_click.assert_called_once_with([100, 200], ctrl=True)
        engine.empty_warehouse.assert_not_called()
        self.assertFalse(getattr(engine, 'vacated_slots', set()))

    def test_stack_count_change_does_not_override_unchanged_source(self):
        before, after = warehouse_stack_frame(), warehouse_stack_frame(after=True, source_retained=True)
        engine, clock = self.engine(after, before)
        with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
            with self.assertRaisesRegex(RuntimeError, '不重复点击'):
                engine.store_item(engine.read_item())
        engine.game.right_click.assert_called_once()

    def test_changing_quantity_marker_needs_three_stable_frames(self):
        before, after = warehouse_stack_frame(), warehouse_stack_frame(after=True)
        pixels = bytearray(after.pixels)
        for x in range(201, 204):
            n = (301 * after.width + x) * 4
            pixels[n:n + 4] = bytes([250, 250, 250, 255])
        changed = Frame(after.width, after.height, bytes(pixels))
        engine, clock = self.engine(after, before)
        phase, frames = {'clicked': False}, cycle((after, changed))
        engine.game.frame.side_effect = lambda: next(frames) if phase['clicked'] else before
        engine.game.right_click.side_effect = lambda *args, **kwargs: phase.update(clicked=True)
        self.assertFalse(same_warehouse_cells(engine.warehouse_cells(after), engine.warehouse_cells(changed)))
        with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
            with self.assertRaisesRegex(RuntimeError, '不重复点击'):
                engine.store_item(engine.read_item())
        engine.game.right_click.assert_called_once()

    def test_single_pixel_noise_is_ignored_by_quantity_marker_check(self):
        before = warehouse_frame(source=False)
        pixels = bytearray(before.pixels)
        n = (310 * before.width + 210) * 4
        pixels[n:n + 4] = bytes([250, 250, 250, 255])
        after = Frame(before.width, before.height, bytes(pixels))
        engine, unused = self.engine(after, before)
        self.assertTrue(same_warehouse_cells(engine.warehouse_cells(before), engine.warehouse_cells(after)))

    def test_execution_stores_then_sorts_and_rescans_before_corrosion(self):
        engine = fake_engine()
        high = classify(0, ['至宝级', '防御力100'])
        low = equipment(1, '稀有')
        engine.scan = Mock(side_effect=[[high, low], [low], []])
        engine.mode, engine.store_item, engine.transfer, engine.commit, engine.sort_inventory = [Mock() for _ in range(5)]
        trace = Mock()
        for name in ('store_item', 'transfer', 'commit', 'sort_inventory'):
            trace.attach_mock(getattr(engine, name), name)
        with patch('engine.sort_button', return_value=[422, 597]):
            engine.execute([high, low])
        self.assertEqual([c[0] for c in trace.mock_calls], ['store_item', 'sort_inventory', 'transfer', 'commit', 'sort_inventory'])

    def test_warehouse_only_preview_runs_and_later_high_quality_drop_is_also_stored(self):
        engine = fake_engine()
        high = classify(0, ['天界级', '材料'])
        new = classify(1, ['神圣级', '未确认类型'])
        engine.scan = Mock(side_effect=[[high], [new], []])
        engine.mode, engine.store_item, engine.transfer, engine.commit, engine.sort_inventory = [Mock() for _ in range(5)]
        with patch('engine.sort_button', return_value=[422, 597]):
            engine.execute([high])
        self.assertEqual(engine.store_item.call_count, 2)
        engine.commit.assert_not_called()

    def test_high_quality_found_during_synthesis_uses_synthesis_sort_checks(self):
        engine = fake_engine()
        high = classify(0, ['天界级', '材料'])
        approved = [equipment(i) for i in range(9)]
        engine.config['max_batches'] = 2
        engine.scan = Mock(side_effect=[approved, [high], []])
        engine.mode, engine.store_item, engine.transfer, engine.commit, engine.sort_inventory = [Mock() for _ in range(5)]
        with patch('engine.sort_button', return_value=[422, 597]):
            engine.execute(approved)
        self.assertEqual(engine.sort_inventory.call_count, 2)
        engine.sort_inventory.assert_called_with('synthesize')
        engine.store_item.assert_called_once_with(high)
        engine.commit.assert_called_once()


class CtrlInputTest(unittest.TestCase):
    def game(self):
        game = Game.__new__(Game)
        game.move, game.wait, game.require_target, game.key, game.right_button, game.require_modifier = [Mock() for _ in range(6)]
        return game

    def test_ctrl_right_click_uses_ctrl_and_releases_it_after_stop(self):
        for interrupted in (False, True):
            game = self.game()
            if interrupted:
                game.wait.side_effect = [None, None, RuntimeError('F10')]
            with patch('windows_io.user') as user:
                user.GetAsyncKeyState.return_value = 0
                if interrupted:
                    with self.assertRaisesRegex(RuntimeError, 'F10'):
                        game.right_click([10, 20], ctrl=True)
                else:
                    game.right_click([10, 20], ctrl=True)
            self.assertEqual([c.args for c in game.key.call_args_list], [(0x11, True), (0x11, False)])
            self.assertEqual([c.args[0] for c in game.right_button.call_args_list], [True, False])

    def test_combined_ctrl_alt_rejected_before_any_input(self):
        game = self.game()
        with self.assertRaises(ValueError):
            game.right_click([10, 20], ctrl=True, alt=True)
        game.move.assert_not_called()
        game.key.assert_not_called()


if __name__ == '__main__':
    unittest.main()
