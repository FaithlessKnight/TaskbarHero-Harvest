"""User tooltips replayed through actual cropping; Windows input is mocked."""
from itertools import cycle
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_regressions_140 import CASES, pair, displaced_pair, place
from engine import cell_box
from rules import classify
from tooltip_regions import item_regions
from vision import header_qualities, header_material_types, header_equipment_stats
from windows_io import Frame
from test_engine import fake_engine
from test_panels import fill_region
import test_warehouse as warehouse
from test_warehouse import warehouse_frame


class TooltipCropRegressionTest(unittest.TestCase):
    def test_supplied_headers_survive_actual_dark_panel_detection(self):
        for name in CASES:
            with self.subTest(name=name):
                before, after, point = pair(name)
                boxes = item_regions(after, before, point, 40, [329, 164, 32, 32])
                self.assertEqual(len(boxes), 1)
                frame = after.crop(boxes[0]).padded()
                expected = '超凡' if name == 'transcendent-bow' else '稀有' if name == 'rare-scroll' else '罕见'
                self.assertEqual(header_qualities(frame), [expected])
                if 'scroll' in name:
                    self.assertEqual(header_material_types(frame), ['铭刻材料'])
                if 'orb' in name:
                    self.assertEqual(header_equipment_stats(frame), ['冷却缩减'])

    def test_displaced_and_bottom_clipped_tooltip_requires_matching_source_sprite(self):
        before, after, point = displaced_pair()
        self.assertFalse(after.tooltip_boxes(before, point, 40))  # Original failure.
        boxes = item_regions(after, before, point, 40, [509, 248, 32, 32])
        self.assertEqual(boxes, [[279, 569, 226, 115]])
        # The neighbouring scroll is not the displayed ore.
        self.assertEqual(item_regions(after, before, [605, 264], 40, [589, 248, 32, 32]), [])

    def test_distant_static_tooltip_cannot_be_attributed_to_new_hover(self):
        before, after, point = displaced_pair()
        self.assertEqual(item_regions(after, after, point, 40, [509, 248, 32, 32]), [])

    def test_source_that_changes_during_hover_does_not_authorize_distant_tooltip(self):
        before, after, point = displaced_pair()
        changed = fill_region(after, [514, 252, 20, 16], (0, 0, 0))
        self.assertEqual(item_regions(changed, before, point, 40, [509, 248, 32, 32]), [])

    def test_two_distant_panels_with_same_sprite_remain_ambiguous(self):
        before, after, point = displaced_pair()
        sprite = after.crop([279, 569, 226, 115])
        after = place(after, sprite, 40, 550)
        self.assertEqual(item_regions(after, before, point, 40, [509, 248, 32, 32]), [])

    def test_normal_comparison_ui_still_uses_only_closest_panel(self):
        before, after, point = pair('uncommon-bow')
        comparison = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'transcendent-bow-1.39.bmp')
        after = place(after, comparison, 390, 100)
        boxes = item_regions(after, before, point, 40, [329, 164, 32, 32])
        self.assertEqual(len(boxes), 2)
        current = after.crop(boxes[0]).padded()
        self.assertEqual(header_qualities(current), ['罕见'])
        self.assertLess(boxes[0][0], 200)

    def test_description_glyphs_cannot_supply_quality_type_or_primary_stat(self):
        frame = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'uncommon-orb-1.39.bmp')
        blank = Frame(240, 500, bytes([0, 0, 0, 255]) * 240 * 500)
        moved = place(blank, frame, 0, 150).padded()
        self.assertEqual(header_qualities(moved), [])
        self.assertEqual(header_material_types(moved), [])
        self.assertEqual(header_equipment_stats(moved), [])

    def test_header_stat_does_not_override_lock_or_conflicting_rarity(self):
        before, after, point = pair('uncommon-orb')
        box = item_regions(after, before, point, 40, [329, 164, 32, 32])[0]
        frame = after.crop(box).padded()
        for extra in (['已锁定'], ['超凡级']):
            item = classify(0, header_equipment_stats(frame) + extra, quality_evidence=header_qualities(frame))
            self.assertEqual(item.action, 'keep')

    def test_ocr_without_rarity_or_readable_type_is_recovered_inside_engine(self):
        for name in CASES:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                before, after, point = pair(name)
                e = fake_engine()
                e.config['points'].update(inventory_first=point, inventory_last=[585, 260])
                e.baseline_frame = before
                e.game.frame.return_value = after
                # Primary attack stat remains readable on bows; the rest is
                # intentionally absent to exercise real header glyph evidence.
                lines = ['物品名称', '攻击伤害1427'] if 'bow' in name else ['物品名称']
                result = {'lines': lines, 'text': '\n'.join(lines)}
                e.read_saved = Mock(side_effect=[({'lines': [], 'text': ''}, 'before.bmp'),
                    (result.copy(), str(Path(folder) / 'item.bmp')), (result.copy(), 'second.bmp'),
                    ({'lines': [], 'text': ''}, 'before2.bmp')])
                item = e.local_item(0, 'inventory', point)
                self.assertEqual(item.action, 'store' if name == 'transcendent-bow' else 'corrode')


class WarehouseReceiptRegressionTest(unittest.TestCase):
    def frames(self, *, retained=False):
        before = warehouse_frame()
        source = before.crop([84, 184, 32, 32])
        # Make the source's central sprite unique, not a solid-color patch.
        source = fill_region(source, [7, 7, 9, 8], (40, 220, 255))
        before = place(before, source, 84, 184)
        after = warehouse_frame(source=False, destination=False)
        after = place(after, source, 184, 284)
        if retained:
            after = place(after, source, 84, 184)
        first = fill_region(after, [224, 324, 32, 32], (100, 180, 50))
        second = fill_region(after, [224, 324, 32, 32], (240, 80, 180))
        return before, first, second

    def test_unrelated_animated_cell_does_not_prevent_confirmed_matching_receipt(self):
        before, first, second = self.frames()
        e, clock = warehouse.WarehouseExecutionTest().engine(first, before)
        clicked = {'value': False}
        frames = cycle((first, second))
        e.game.frame.side_effect = lambda: next(frames) if clicked['value'] else before
        e.game.right_click.side_effect = lambda *args, **kwargs: clicked.update(value=True)
        with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
            self.assertTrue(e.store_current_page(classify(0, ['超凡级', '攻击伤害1427'])))
        self.assertEqual(e.vacated_slots, {0})
        e.game.right_click.assert_called_once_with([100, 200], ctrl=True)

    def test_receipt_without_empty_source_never_claims_success(self):
        before, first, second = self.frames(retained=True)
        e, clock = warehouse.WarehouseExecutionTest().engine(first, before)
        with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
            with self.assertRaisesRegex(RuntimeError, '不重复点击'):
                e.store_current_page(classify(0, ['超凡级', '攻击伤害1427']))
        e.game.right_click.assert_called_once()

    def test_wrong_sprite_with_animation_is_not_accepted_as_receipt(self):
        before, first, second = self.frames()
        first = fill_region(first, [184, 284, 32, 32], (20, 80, 210))
        second = fill_region(second, [184, 284, 32, 32], (20, 80, 210))
        e, clock = warehouse.WarehouseExecutionTest().engine(first, before)
        clicked = {'value': False}
        frames = cycle((first, second))
        e.game.frame.side_effect = lambda: next(frames) if clicked['value'] else before
        e.game.right_click.side_effect = lambda *args, **kwargs: clicked.update(value=True)
        with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
            with self.assertRaisesRegex(RuntimeError, '不重复点击'):
                e.store_current_page(classify(0, ['超凡级', '攻击伤害1427']))
        e.game.right_click.assert_called_once()


if __name__ == '__main__':
    unittest.main()
