import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rules import batches, classify, has_item_details, parse_level, parse_stack_repetition


def equipment(quality='传奇', level='Lv.90', slot=0):
    return classify(slot, ['次元靴', quality + '级', '防御力 660', '固有属性', '装饰槽',
                           level.replace('Lv.', '需要等级')])


class RulesTest(unittest.TestCase):
    def test_source_code_and_combat_log_are_not_item_tooltips(self):
        self.assertFalse(has_item_details(['获得了普通宝箱（木头人）', "quality='传奇级'", '材料']))
        self.assertFalse(has_item_details(['背包', '自动填充', '10,629,463']))
        self.assertTrue(has_item_details(['次元靴', '传 奇 级', '防御力660']))
        self.assertTrue(has_item_details(['小南瓜头糖果', '供 奉 材 料']))
        self.assertFalse(has_item_details(['传奇级', '装饰槽'], ['传奇级', '装饰槽']))

    def test_offering_storage_overrides_low_quality_corrosion(self):
        item = classify(0, ['小南瓜头糖果', '罕见级', '✦ 供 奉 材 料', '材料'])
        self.assertEqual((item.action, item.kind, item.material_type), ('store', 'material', 'offering'))
        self.assertIn('供奉', item.reason)

    def test_offering_in_full_screen_is_a_veto(self):
        item = classify(0, ['普通级', '材料'], full_text='普通级 材料 供 奉')
        self.assertEqual(item.action, 'keep')

    def test_all_three_low_qualities_for_equipment_and_materials(self):
        for quality in ('普通', '罕见', '稀有'):
            with self.subTest(quality=quality):
                self.assertEqual(equipment(quality).action, 'corrode')
                self.assertEqual(classify(0, ['矿石', quality + '级', '✦制作材料']).action, 'corrode')

    def test_weapon_damage_line_confirms_type_without_socket(self):
        item = classify(0, ['次元之剑', '稀有级', '攻击伤害360', '每秒攻击1.20'])
        self.assertEqual((item.action, item.kind), ('corrode', 'equipment'))
        self.assertEqual(classify(0, ['稀有级', '可提高攻击伤害360']).action, 'keep')

    def test_legendary_and_immortal_equipment_do_not_require_level(self):
        for quality in ('传奇', '不朽'):
            for level in ('Lv.90', '', 'Lv.80 Lv.90'):
                self.assertEqual(equipment(quality, level=level).action, 'synthesize')

    def test_material_description_is_not_a_type(self):
        self.assertEqual(classify(0, ['普通级', '可用于合成材料']).action, 'keep')
        self.assertEqual(classify(0, ['普通级', '一 制 作 材 料']).action, 'corrode')
        self.assertEqual(classify(0, ['传奇級', '一 制 作 材 料']).kind, 'material')

    def test_legend_material_and_higher_quality_are_kept(self):
        self.assertEqual(classify(0, ['传奇级', '材料']).action, 'corrode')
        self.assertEqual(equipment('神圣').action, 'store')

    def test_legendary_and_immortal_crafting_materials_are_corroded(self):
        for quality in ('传奇', '不朽'):
            for label in ('制作材料', '一 制 作 材 料', '✦製作材料'):
                with self.subTest(quality=quality, label=label):
                    item = classify(0, ['星尘锭', quality + '级', label])
                    self.assertEqual((item.action, item.kind, item.crafting), ('corrode', 'material', True))
            self.assertEqual(classify(0, [quality + '级', '材料']).action, 'corrode')
            self.assertEqual(classify(0, [quality + '级', '装饰材料']).action, 'corrode')
            self.assertEqual(classify(0, [quality + '级', '制作材料', '供奉']).action, 'keep')
            self.assertEqual(classify(0, [quality + '级', '制作材料', '已锁定']).action, 'keep')
        self.assertEqual(equipment('不朽').action, 'synthesize')
        self.assertEqual(equipment('传奇').action, 'synthesize')
        self.assertEqual(classify(0, ['天界级', '制作材料']).action, 'store')

    def test_material_description_cannot_expand_high_quality_rule(self):
        self.assertEqual(classify(0, ['不朽级', '可以用于制作材料']).action, 'keep')

    def test_stack_repetition_requires_exact_bounded_identical_digits(self):
        for count in range(2, 6):
            for copies in (5, 8):
                self.assertEqual(parse_stack_repetition(' '.join(str(count) * copies), copies), count)
        for text in ('', '11111', '66666', '12345', '2222', '222222', '2222Z', 'Lv22222'):
            self.assertIsNone(parse_stack_repetition(text, 5))

    def test_small_five_ocr_alias_is_limited_to_exact_quantity_runs(self):
        for text, copies in (('sssss', 5), ('SSSSSSSS', 8), ('5 s 5 S 5', 5)):
            self.assertEqual(parse_stack_repetition(text, copies), 5)
        for text in ('ssss', 'ssssss', 'sss2s', 'SILVER', 'stack5', '55556'):
            self.assertIsNone(parse_stack_repetition(text, 5))

    def test_unknown_and_conflicting_rarity_are_kept(self):
        self.assertEqual(classify(0, ['普通级', '罕见级', '材料']).action, 'keep')
        self.assertEqual(classify(0, ['普通', '罕见', '材料']).action, 'keep')
        self.assertEqual(classify(0, ['材料']).action, 'keep')

    def test_actual_ocr_shape_with_missing_rarity_suffix(self):
        self.assertEqual(classify(0, ['次 元 靴', '传 奇', '防 御 力', '固 有 届 性',
                                      '需 要 等 级 9 0']).action, 'synthesize')

    def test_isolated_level_numbers_only(self):
        self.assertEqual(parse_level('90'), 90)
        self.assertIsNone(parse_level('I-t.Ei0'))
        self.assertIsNone(parse_level('90 80'))
        self.assertIsNone(parse_level('0'))

    def test_lock_shortcut_is_not_locked_state(self):
        self.assertEqual(classify(0, ['普通级', '制作材料', 'ALT + 鼠标 锁定']).action, 'corrode')
        self.assertEqual(classify(0, ['普通级', '材料', '已锁定']).action, 'keep')

    def test_baseline_rarity_cannot_classify_an_empty_slot(self):
        self.assertEqual(classify(0, ['普通级', '材料'], ['普通级', '材料']).action, 'keep')

    def test_material_and_equipment_type_conflict(self):
        self.assertEqual(classify(0, ['普通级', '材料', '装备槽']).action, 'keep')

    def test_nine_same_quality_can_have_different_levels(self):
        items = [equipment(level='Lv.80', slot=n) for n in range(10)]
        items += [equipment(level='Lv.90', slot=10 + n) for n in range(8)]
        result = batches(items)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(len(batch) == 9 for action, batch in result))
        self.assertEqual({i.level for i in result[0][1]}, {80})
        self.assertEqual({i.level for i in result[1][1]}, {80, 90})

    def test_corrosion_batches_precede_synthesis(self):
        items = [equipment(slot=n) for n in range(9)]
        items += [equipment('普通', slot=n + 9) for n in range(10)]
        self.assertEqual([(action, len(items)) for action, items in batches(items)],
                         [('corrode', 9), ('corrode', 1), ('synthesize', 9)])

    def test_identity_retains_stats_but_not_currency_only_line(self):
        first = classify(0, ['普通级', '材料', '12,000'])
        second = classify(12, ['普通级', '材料', '12,001'])
        self.assertEqual(first.identity(), second.identity())
        self.assertNotEqual(equipment(level='Lv.80').identity(), equipment(level='Lv.90').identity())


if __name__ == '__main__':
    unittest.main()
