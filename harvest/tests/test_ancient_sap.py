"""Saved sap tooltip regression: header glyphs, default rules and custom scopes."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_regressions_140 import place
from rules import classify
from supplemental import new_rule, apply_rules
from vision import asset, header_qualities, header_material_types
from windows_io import Frame
from test_engine import fake_engine


FIXTURES = Path(__file__).parent / 'fixtures'
FILES = ('ancient-tree-sap-1.48.bmp', 'ancient-tree-sap-local-1.48.bmp')


def ocr_result(*lines):
    return {'lines': list(lines), 'text': '\n'.join(lines)}


class AncientSapTest(unittest.TestCase):
    def test_supplied_and_failed_run_headers_confirm_rare_engraving_material(self):
        for filename in FILES:
            with self.subTest(filename=filename):
                frame = Frame.load_bmp(FIXTURES / filename)
                if 'local' not in filename:
                    frame = frame.padded()
                self.assertEqual(header_qualities(frame), ['稀有'])
                self.assertEqual(header_material_types(frame), ['雕刻材料'])
                item = classify(0, header_material_types(frame) + ['古树树液', '怖有'],
                                quality_evidence=header_qualities(frame))
                self.assertEqual((item.action, item.quality, item.kind, item.material_type),
                                 ('corrode', '稀有', 'material', 'engraving'))

    def test_missing_quality_ocr_is_recovered_without_extra_header_reads(self):
        for filename in FILES:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as folder:
                e = fake_engine()
                sprite = Frame.load_bmp(FIXTURES / filename)
                e.baseline_frame = Frame(700, 700, bytes([75, 85, 95, 255]) * 700 * 700)
                e.game.frame.return_value = place(e.baseline_frame, sprite, 100, 100)
                e.config['points'].update(inventory_first=[345, 180], inventory_last=[585, 260])
                e.read_saved = Mock(side_effect=[(ocr_result(), 'before.bmp'),
                    (ocr_result('古树树液', '怖有', '《0雕刻材料'), str(Path(folder) / 'item.bmp')),
                    (ocr_result('古树树液', '0雕刻材料'), 'second.bmp'), (ocr_result(), 'before2.bmp')])
                e.enhance_header = Mock(side_effect=AssertionError('no additional header OCR required'))
                e.rarity_lines = Mock(side_effect=AssertionError('quality glyph was recovered'))
                item = e.local_item(0, 'inventory', [345, 180])
                self.assertEqual((item.action, item.quality, item.kind, item.material_type),
                                 ('corrode', '稀有', 'material', 'engraving'))
                self.assertEqual(e.read_saved.call_count, 4)
                e.enhance_header.assert_not_called()
                e.game.right_click.assert_not_called()

    def test_material_checkbox_still_controls_default_corrosion(self):
        item = classify(0, ['古树树液', '雕刻材料'], quality_evidence=['稀有'],
                        selection={'equipment': ['稀有'], 'material': []})
        self.assertEqual((item.action, item.kind, item.material_type), ('keep', 'material', 'engraving'))

    def test_exact_sap_rule_can_corrode_or_store_only_matching_quality(self):
        for action in ('corrode', 'store'):
            item = classify(0, ['古树树液', '雕刻材料'], quality_evidence=['稀有'], selection={})
            rule = new_rule('古树树液', 'material', action, ['稀有'])
            got = apply_rules(item, [rule], ['古树树液'] * 2)
            self.assertEqual((got.action, got.custom_rule), (action, rule['id']))
            wrong_quality = dict(rule, qualities=['传奇'])
            self.assertFalse(apply_rules(item, [wrong_quality], ['古树树液'] * 2).custom_rule)

    def test_leaf_rule_cannot_match_sap_but_default_checkbox_still_applies(self):
        item = classify(0, ['古树树液', '雕刻材料'], quality_evidence=['稀有'])
        rule = new_rule('古树树叶', 'material', 'store', ['稀有'])
        got = apply_rules(item, [rule], ['古树树液'] * 2)
        self.assertEqual(got.action, 'corrode')
        self.assertFalse(got.custom_rule)

    def test_conflicts_and_locks_still_keep_the_material(self):
        for extra in ('天界级', '铭刻材料', '已锁定'):
            with self.subTest(extra=extra):
                item = classify(0, ['古树树液', '雕刻材料', extra], quality_evidence=['稀有'])
                self.assertEqual(item.action, 'keep')
                rule = new_rule('古树树液', 'material', 'corrode')
                self.assertEqual(apply_rules(item, [rule], ['古树树液'] * 2).action, 'keep')

    def test_title_and_effects_cannot_supply_quality_or_material_type(self):
        for top in (8, 160):
            frame = Frame(234, 240, bytes([0, 0, 0, 255]) * 234 * 240)
            frame = place(frame, asset('rarity-rare-sap'), 63, top)
            frame = place(frame, asset('material-engraving'), 75, top + 16)
            self.assertEqual(header_qualities(frame), [])
            self.assertEqual(header_material_types(frame), [])

    def test_incomplete_quality_or_type_glyphs_are_not_accepted(self):
        for name, detector, x, y in (('rarity-rare-sap', header_qualities, 63, 49),
                                      ('material-engraving', header_material_types, 75, 67)):
            frame = Frame(234, 180, bytes([0, 0, 0, 255]) * 234 * 180)
            glyph = asset(name)
            frame = place(frame, glyph.crop([0, 0, glyph.width - 8, glyph.height]), x, y)
            self.assertEqual(detector(frame), [])


if __name__ == '__main__':
    unittest.main()
