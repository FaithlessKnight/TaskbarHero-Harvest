from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from supplemental import (apply_rules, allowed, new_rule, normalize_rules, title_from_crop,
                          title_from_details, title_view, resolved_readings)
from rules import classify
from test_engine import fake_engine
from check_regressions_140 import pair


def result(lines, title=None):
    value = {'lines': lines, 'text': '\n'.join(lines)}
    if title:
        value['details'] = [{'text': title, 'words': [{'box': [10, 10, 64, 15]}]}]
    return value


class SupplementalTest(unittest.TestCase):
    def test_explicit_routing_overrides_defaults_in_both_directions(self):
        for name, lines, kind, action in (
                ('天界弓', ['天界弓', '天界级', '装备槽'], 'equipment', 'corrode'),
                ('铁锭', ['铁锭', '普通级', '制作材料'], 'material', 'store'),
                ('瘟疫之果', ['瘟疫之果', '稀有级'], 'material', 'store'),
                ('供奉香', ['供奉香', '供奉材料'], 'material', 'corrode')):
            with self.subTest(name=name):
                rule = new_rule(name, kind, action)
                item = apply_rules(classify(0, lines), [rule], [name, name])
                self.assertEqual((item.action, item.name, item.custom_rule), (action, name, rule['id']))
                self.assertTrue(allowed(item, [rule], action))
                e = fake_engine()
                e.config['supplemental_items'] = [rule]
                self.assertTrue(e.action_allowed(action, item))
                self.assertFalse(e.action_allowed('synthesize', item))

    def test_exact_title_and_scope_no_substrings_or_description(self):
        rule = new_rule('银锭', 'material', 'store', ['稀有'])
        item = classify(0, ['银锭', '稀有级', '制作材料'])
        for reads in (['银锭', '银锭描述'], ['大银锭'] * 2, ['材料'] * 2, [''] * 2):
            got = apply_rules(item, [rule], reads)
            self.assertFalse(got.custom_rule)
        self.assertTrue(apply_rules(item, [rule], ['银锭'] * 2).custom_rule)
        other = classify(0, ['银锭', '普通级', '制作材料', '银锭用于锻造'])
        self.assertEqual(apply_rules(other, [rule], ['银锭'] * 2).action, 'corrode')

    def test_uncertain_registered_name_never_falls_through_to_corrosion(self):
        rule = new_rule('银锭', 'material', 'store')
        item = classify(0, ['银锭', '普通级', '制作材料'])
        for reads in (['银锭', '银绽'], ['', '银锭'], ['银锭']):
            self.assertEqual(apply_rules(item, [rule], reads).action, 'keep')

    def test_unknown_quality_and_type_can_use_exact_name_all_scope(self):
        rule = new_rule('古怪石头', 'material', 'corrode')
        item = apply_rules(classify(0, ['古怪石头']), [rule], ['古怪石头'] * 2)
        self.assertEqual((item.action, item.kind, item.quality), ('corrode', 'material', ''))
        specific = dict(rule, qualities=['普通'])
        self.assertEqual(apply_rules(classify(0, ['古怪石头']), [specific], ['古怪石头'] * 2).action, 'keep')

    def test_locked_equipped_rarity_and_type_conflicts_still_block(self):
        rule = new_rule('银锭', 'material', 'corrode')
        for lines in (['银锭', '已锁定'], ['银锭', '已装备'], ['银锭', 'EQUIPPED'],
                      ['银锭', '稀有级', '至宝级', '制作材料'], ['银锭', '稀有级', '装备槽'],
                      ['银锭', '天界级', '制作材料', '装备槽'],
                      ['银锭', '天界级', '制作材料', '铭刻材料']):
            with self.subTest(lines=lines):
                self.assertEqual(apply_rules(classify(0, lines), [rule], ['银锭'] * 2).action, 'keep')

    def test_deleting_changing_or_restricting_rule_invalidates_old_item(self):
        rule = new_rule('银锭', 'material', 'store')
        item = apply_rules(classify(0, ['银锭', '普通级', '制作材料']), [rule], ['银锭'] * 2)
        for rows in ([], [dict(rule, action='corrode')], [dict(rule, name='铜锭')],
                     [dict(rule, kind='equipment')], [dict(rule, qualities=['稀有'])]):
            self.assertFalse(allowed(item, rows, 'store'))
        self.assertFalse(allowed(replace(item, text=item.text + '\n已锁定'), [rule], 'store'))

    def test_validations_and_disjoint_scopes(self):
        rule = new_rule('银锭', 'material', 'store', ['稀有', '普通', '稀有'])
        self.assertEqual(rule['qualities'], ['普通', '稀有'])
        other = dict(rule, id='second', qualities=['传奇'])
        self.assertEqual(len(normalize_rules([rule, other])), 2)
        for other in (dict(rule, id='second'), dict(rule, id='second', qualities=[]),
                      dict(rule, name=''), dict(rule, kind=[]), dict(rule, action={}),
                      dict(rule, qualities=[[]]), dict(rule, qualities='普通'), dict(rule, extra=1)):
            with self.subTest(other=other), self.assertRaises(ValueError):
                normalize_rules([rule, other])

    def test_only_single_title_line_with_title_coordinates_is_used(self):
        good = result(['银锭'], '银锭')
        self.assertEqual(title_from_details(good), '银锭')
        for box in ([10, 38, 30, 12], [10, 20, 30, 20], [0, 10, 30, 12]):
            got = result(['银锭'], '银锭')
            got['details'][0]['words'][0]['box'] = box
            self.assertEqual(title_from_details(got), '')
        self.assertEqual(title_from_details(result(['说明中提及银锭'])), '')
        self.assertEqual(title_from_crop(result(['银锭', '普通级'])), '')
        self.assertEqual(title_from_crop(result(['制作材料'])), '')

    def test_recovered_exact_pair_overrides_missed_character_without_fuzzy_matching(self):
        rules = [new_rule('黑钢锭', 'material', 'store'), new_rule('银锭', 'material', 'corrode')]
        self.assertEqual(resolved_readings([['黑锯', '黑锯孬'], ['黑锭'] * 2, ['黑钢锭'] * 2], rules), ['黑钢锭'] * 2)
        self.assertEqual(resolved_readings([['银锭', '锟锭'], ['银锭', ''], ['银锭'] * 2], rules), ['银锭'] * 2)
        # No approximate alias from the truncated name alone.
        got = apply_rules(classify(0, ['黑钢锭', '超凡级', '制作材料']), rules,
                          resolved_readings([['黑锭'] * 2], rules))
        self.assertFalse(got.custom_rule)
        self.assertEqual(resolved_readings([['银锭', ''], ['银绽'] * 2], rules), ['银锭', ''])
        conflicting = resolved_readings([['银锭'] * 2, ['黑钢锭'] * 2], rules)
        self.assertEqual(set(conflicting), {'银锭', '黑钢锭'})

    def test_tight_title_view_excludes_quality_and_description(self):
        from windows_io import Frame
        frame = Frame.load_bmp(Path(__file__).parent / 'fixtures' / 'blacksteel-ingot-1.47.bmp').padded()
        view = title_view(frame)
        self.assertEqual(view.height, 18)
        self.assertLess(view.width, frame.width)
        self.assertIsNone(title_view(Frame(80, 20, bytes(80 * 20 * 4))))


class SupplementalEngineTest(unittest.TestCase):
    def engine(self, folder, title='银锭', action='store', details=True):
        e = fake_engine()
        before, after, point = pair('rare-scroll')
        e.config['points'].update(inventory_first=point, inventory_last=[585, 260])
        e.config['supplemental_items'] = [new_rule(title, 'material', action)]
        e.baseline_frame = before
        e.game.frame.return_value = after
        e.rarity_lines = Mock(return_value=[])
        e.enhance_header = Mock(return_value=None)
        current = result([title, '稀有级', '铭刻材料'], title if details else None)
        e.read_saved = Mock(side_effect=[(result([]), 'before.bmp'),
            (current, str(Path(folder) / 'item.bmp')), (current.copy(), 'second.bmp'), (result([]), 'before2.bmp'),
            (result([title]), 'title4.bmp'), (result([title]), 'title5.bmp')])
        return e, point

    def test_framed_title_overrides_inscription_corrosion_without_more_ocr(self):
        with tempfile.TemporaryDirectory() as folder:
            e, point = self.engine(folder)
            item = e.local_item(0, 'inventory', point)
            self.assertEqual((item.action, item.name), ('store', '银锭'))
            self.assertEqual(e.read_saved.call_count, 4)
            info = json.loads((Path(folder) / 'item.json').read_text(encoding='utf-8'))
            self.assertEqual(info['supplemental']['title_readings'], ['银锭'] * 2)

    def test_missing_details_uses_two_isolated_title_crops(self):
        with tempfile.TemporaryDirectory() as folder:
            e, point = self.engine(folder, details=False)
            item = e.local_item(0, 'inventory', point)
            self.assertEqual(item.action, 'store')
            self.assertEqual([c.kwargs for c in e.read_saved.call_args_list[-2:]],
                             [dict(scale=4, interpolation='cubic'), dict(scale=5, interpolation='cubic')])

    def test_no_registered_rules_no_added_title_ocr(self):
        with tempfile.TemporaryDirectory() as folder:
            e, point = self.engine(folder, details=False)
            e.config.pop('supplemental_items')
            item = e.local_item(0, 'inventory', point)
            self.assertEqual(item.action, 'corrode')
            self.assertEqual(e.read_saved.call_count, 4)

    def test_unframed_crop_never_routes_from_a_body_line(self):
        with tempfile.TemporaryDirectory() as folder:
            e, point = self.engine(folder)
            with patch('tooltip_regions.item_regions', return_value=[[100, 100, 226, 190]]):
                item = e.local_item(0, 'inventory', point)
            self.assertFalse(item.custom_rule)
            self.assertEqual(e.read_saved.call_count, 4)

    def test_custom_material_corrosion_unknown_quantity_allows_single_add(self):
        e = fake_engine()
        rule = new_rule('银锭', 'material', 'corrode')
        e.config['supplemental_items'] = [rule]
        e.local_item = Mock(return_value=apply_rules(classify(0, ['银锭', '稀有级', '制作材料']), [rule], ['银锭'] * 2))
        e.read_stack_count.return_value = None
        item = e.read_item(0, [])
        self.assertEqual(item.action, 'corrode')
        self.assertIsNone(item.count)

    def test_two_agreed_but_incomplete_names_use_masked_title_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            e, point = self.engine(folder, title='黑钢锭', details=False)
            e.read_saved.side_effect = [(result([]), 'before.bmp'),
                (result(['黑钢锭', '稀有级', '铭刻材料']), str(Path(folder) / 'item.bmp')),
                (result(['黑钢锭', '稀有级', '铭刻材料']), 'second.bmp'), (result([]), 'before2.bmp'),
                (result(['黑锭']), 'old4.bmp'), (result(['黑锭']), 'old5.bmp'),
                (result(['黑钢锭']), 'mask4.bmp'), (result(['黑钢锭']), 'mask5.bmp')]
            item = e.local_item(0, 'inventory', point)
            self.assertEqual((item.name, item.action), ('黑钢锭', 'store'))
            info = json.loads((Path(folder) / 'item.json').read_text(encoding='utf-8'))
            self.assertEqual(info['supplemental']['title_attempts'][-2:], [['黑锭'] * 2, ['黑钢锭'] * 2])

    def test_blank_mask_reads_can_use_tight_color_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            e, point = self.engine(folder, details=False)
            e.read_saved.side_effect = [(result([]), 'before.bmp'),
                (result(['银锭', '稀有级', '铭刻材料']), str(Path(folder) / 'item.bmp')),
                (result(['银锭', '稀有级', '铭刻材料']), 'second.bmp'), (result([]), 'before2.bmp'),
                (result(['银锭']), 'old4.bmp'), (result([]), 'old5.bmp'),
                (result([]), 'mask4.bmp'), (result([]), 'mask5.bmp'),
                (result(['银锭']), 'tight4.bmp'), (result(['银锭']), 'tight5.bmp')]
            item = e.local_item(0, 'inventory', point)
            self.assertEqual((item.action, item.name), ('store', '银锭'))
            self.assertEqual(e.read_saved.call_count, 10)


if __name__ == '__main__':
    unittest.main()
