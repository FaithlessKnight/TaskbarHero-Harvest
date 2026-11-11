from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rules import classify, has_item_details


class CommonEquipmentTest(unittest.TestCase):
    def test_observed_bow_arrow_and_helmet_ocr_are_equipment(self):
        for lines in (
            ['暗影之弓', '普 通', '． 0 攻 击 伤 害', '9', '每秒攻击'],
            ['次元箭', '普 通', '攻 击 遽 度', '攻 击 速 度'],
            ['次元头盔', '普 通', '0 《 防 御 力', '0'],
            ['暗影之弓', '普 通 報', '攻击伤害', '239', '每秒攻击1.50'],
        ):
            with self.subTest(lines=lines):
                item = classify(0, lines)
                self.assertEqual((item.action, item.quality, item.kind), ('corrode', '普通', 'equipment'))

    def test_each_stat_can_confirm_an_equipment_without_sockets(self):
        for label in ('防御力', '攻击力', '攻击伤害', '攻击速度', '格挡几率'):
            for prefix in ('', '．0', '0《'):
                for value in ('', '239', '+1.50%'):
                    with self.subTest(label=label, prefix=prefix, value=value):
                        self.assertEqual(classify(0, ['普通级', prefix + label + value]).kind, 'equipment')

    def test_material_stats_and_descriptions_do_not_turn_into_equipment(self):
        for label in ('攻击速度10%', '0《防御力239', '．0攻击伤害'):
            self.assertEqual(classify(0, ['普通级', '装饰材料', label]).kind, 'material')
        for label in ('可提高攻击速度10%', '每次造成攻击伤害239', '攻击速度提高10%', '攻击伤害材料', '暗影之弓'):
            self.assertEqual(classify(0, ['普通级', label]).action, 'keep')

    def test_missing_quality_and_explicit_corrosion_choice_are_respected(self):
        self.assertEqual(classify(0, ['暗影之弓', '．0攻击伤害239']).action, 'keep')
        self.assertEqual(classify(0, ['普通级', '0《防御力239'], selection={}).action, 'keep')
        self.assertEqual(classify(0, ['普通级', '．0攻击伤害239', '已锁定']).action, 'keep')
        self.assertEqual(classify(0, ['至宝级', '．0攻击伤害239']).action, 'store')

    def test_observed_rarity_suffix_requires_a_short_header_line(self):
        self.assertTrue(has_item_details(['普通報', '攻击伤害']))
        self.assertEqual(classify(0, ['普通報', '攻击伤害']).quality, '普通')
        self.assertEqual(classify(0, ['描述中的普通報', '攻击伤害']).action, 'keep')
        self.assertEqual(classify(0, ['普通報', '至宝级', '攻击伤害']).action, 'keep')


if __name__ == '__main__':
    unittest.main()
