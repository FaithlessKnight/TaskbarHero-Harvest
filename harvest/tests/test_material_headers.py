"""Regressions from the supplied decoration and inscription tooltips."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rules import (classify, selected_for_corrosion, QUALITY_NAMES, OTHER_QUALITIES,
                   INSCRIPTION_STORAGE_QUALITIES, has_item_details)
from vision import header_qualities, header_material_types
from windows_io import Frame
from test_engine import fake_engine
from test_panel_cleanup import place


FIXTURES = Path(__file__).parent / 'fixtures'


class MaterialHeaderTest(unittest.TestCase):
    def test_all_four_supplied_headers_recover_missing_ocr(self):
        for name in ('decoration-jade', 'decoration-amber', 'decoration-obsidian', 'inscription-scroll'):
            with self.subTest(name=name):
                frame = Frame.load_bmp(FIXTURES / (name + '.bmp')).padded()
                inscription = name == 'inscription-scroll'
                self.assertEqual(header_qualities(frame), ['稀有' if inscription else '罕见'])
                self.assertEqual(header_material_types(frame), ['铭刻材料' if inscription else '装饰材料'])

    def test_actual_local_recognition_recovers_omitted_or_misread_quality_and_type(self):
        for name in ('decoration-jade', 'decoration-amber', 'decoration-obsidian', 'inscription-scroll'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                frame = Frame.load_bmp(FIXTURES / (name + '.bmp'))
                e = fake_engine()
                e.baseline_frame = Frame(frame.width, frame.height, bytes([0, 0, 0, 255]) * frame.width * frame.height)
                e.game.frame.return_value = Mock(spec=Frame)
                e.game.frame.return_value.tooltip_boxes.return_value = [[0, 0, frame.width, frame.height]]
                e.game.frame.return_value.crop.return_value = frame
                lines = ['稀有铭文卷轴', '怖有圾', '过刻材料'] if name == 'inscription-scroll' else ['宝石', '1罕见级', '0装饰材料']
                result = {'lines': lines, 'text': '\n'.join(lines)}
                e.read_saved = Mock(side_effect=[({'lines': [], 'text': ''}, 'before.bmp'),
                    (result.copy(), str(Path(folder) / 'current.bmp')),
                    (result.copy(), 'second.bmp'), ({'lines': [], 'text': ''}, 'before2.bmp')])
                item = e.local_item(0, 'inventory', [100, 200])
                self.assertEqual((item.action, item.kind, item.material_type),
                                 ('corrode', 'material', 'inscription' if name == 'inscription-scroll' else 'decoration'))
                self.assertEqual(e.read_saved.call_count, 4)  # No extra OCR retries needed.

    def test_header_glyphs_in_title_or_effect_list_do_not_authorize_processing(self):
        source = Frame.load_bmp(FIXTURES / 'inscription-scroll.bmp')
        for top in (0, 120):
            with self.subTest(top=top):
                blank = Frame(source.width, source.height, bytes([0, 0, 0, 255]) * source.width * source.height)
                moved = place(blank, source.crop([60, 40, 65, 35]), 60, top)
                self.assertEqual(header_qualities(moved), [])
                self.assertEqual(header_material_types(moved), [])

    def test_glyph_evidence_does_not_override_conflicting_quality(self):
        frame = Frame.load_bmp(FIXTURES / 'inscription-scroll.bmp').padded()
        item = classify(0, header_qualities(frame) + header_material_types(frame) + ['至宝级'])
        self.assertEqual(item.action, 'keep')

    def test_supplemental_quality_cannot_hide_conflict_at_end_of_original_header(self):
        item = classify(0, ['宝石', '0', '.', '-', '0', '至宝级', '装饰材料'],
                        quality_evidence=['罕见'])
        self.assertEqual(item.action, 'keep')

    def test_supplemental_evidence_preserves_plague_fruit_title_priority(self):
        item = classify(0, ['瘟疫之果', '装饰材料'], quality_evidence=['至宝'])
        self.assertEqual((item.action, item.material_type), ('corrode', 'plague_fruit'))

    def test_fallback_rarity_preserves_confirmed_material_type(self):
        with tempfile.TemporaryDirectory() as folder:
            frame = Frame.load_bmp(FIXTURES / 'inscription-scroll.bmp')
            # Remove only rarity; material glyph remains genuine.
            from test_panels import fill_region
            frame = fill_region(frame, [58, 39, 45, 16], (0, 0, 0))
            e = fake_engine()
            e.baseline_frame = Frame(frame.width, frame.height, bytes([0, 0, 0, 255]) * frame.width * frame.height)
            e.game.frame.return_value = Mock(spec=Frame)
            e.game.frame.return_value.tooltip_boxes.return_value = [[0, 0, frame.width, frame.height]]
            e.game.frame.return_value.crop.return_value = frame
            result = {'lines': ['卷轴', '过刻材料'], 'text': '卷轴\n过刻材料'}
            e.read_saved = Mock(side_effect=[({'lines': [], 'text': ''}, 'before.bmp'),
                (result.copy(), str(Path(folder) / 'current.bmp')), (result.copy(), 'second.bmp'),
                ({'lines': [], 'text': ''}, 'before2.bmp')])
            e.rarity_lines = Mock(return_value=['普通'])
            item = e.local_item(0, 'inventory', [100, 200])
            self.assertEqual((item.action, item.material_type), ('corrode', 'inscription'))


class InscriptionRulesTest(unittest.TestCase):
    def test_each_known_quality_routes_to_correct_shortcut_independent_of_boxes(self):
        for quality in QUALITY_NAMES + OTHER_QUALITIES:
            for selection in ({}, {'material': list(QUALITY_NAMES) + ['不朽', '至宝']}):
                with self.subTest(quality=quality, selection=selection):
                    item = classify(0, ['铭文卷轴', quality + '级', '铭刻材料'], selection=selection)
                    expected = 'store' if quality in INSCRIPTION_STORAGE_QUALITIES else 'corrode'
                    self.assertEqual((item.action, item.kind, item.material_type), (expected, 'material', 'inscription'))
                    e = fake_engine()
                    e.config['corrosion_selection'] = selection
                    self.assertTrue(e.action_allowed(expected, item))
                    self.assertFalse(e.action_allowed('corrode' if expected == 'store' else 'store', item))
                    self.assertEqual(selected_for_corrosion(item, selection), expected == 'corrode')
                    self.assertFalse(e.action_allowed('synthesize', item))

    def test_locks_quality_conflicts_unknown_quality_and_type_conflicts_are_kept(self):
        for lines in (['稀有级', '铭刻材料', '已锁定'], ['稀有级', '传奇级', '铭刻材料'],
                      ['未知级', '铭刻材料'], ['传奇级', '铭刻材料', '装饰材料'],
                      ['稀有级', '铭刻材料', '装备槽']):
            with self.subTest(lines=lines):
                self.assertEqual(classify(0, lines).action, 'keep')

    def test_descriptions_and_item_names_cannot_supply_material_type_or_quality(self):
        for lines in (['稀有级', '可用于铭刻材料'], ['稀有铭文卷轴', '铭刻材料'],
                      ['传奇级', '铭刻材料效果列表']):
            self.assertEqual(classify(0, lines).action, 'keep')
        self.assertTrue(has_item_details(['铭刻材料']))

    def test_inscription_is_distinct_from_existing_engraving_material(self):
        item = classify(0, ['传奇级', '雕刻材料'])
        self.assertEqual((item.action, item.material_type), ('corrode', 'engraving'))
        item = classify(0, ['传奇级', '铭刻材料'])
        self.assertEqual((item.action, item.material_type), ('store', 'inscription'))

    def test_low_inscription_unknown_quantity_keeps_single_corrosion_high_stores(self):
        for quality, action in (('稀有', 'corrode'), ('传奇', 'store')):
            e = fake_engine()
            e.local_item = Mock(return_value=classify(0, [quality + '级', '铭刻材料']))
            e.read_stack_count.return_value = None
            item = e.read_item(0, [])
            self.assertEqual(item.action, action)
            self.assertEqual(e.read_stack_count.call_count, 1 if quality == '稀有' else 0)
            if quality == '稀有':
                self.assertIsNone(item.count)
                self.assertIn('仅 Alt＋右键添加一次', item.reason)

    def test_offering_and_plague_fruit_priorities_remain(self):
        self.assertEqual(classify(0, ['供奉材料', '稀有级', '铭刻材料']).action, 'store')
        item = classify(0, ['瘟疫之果', '宇宙级', '铭刻材料'])
        self.assertEqual((item.action, item.material_type), ('corrode', 'plague_fruit'))


if __name__ == '__main__':
    unittest.main()
