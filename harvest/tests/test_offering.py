from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rules import classify, selected_for_corrosion, has_item_details
from test_engine import fake_engine
from test_streaming import runner
import test_warehouse


class OfferingStorageTest(unittest.TestCase):
    def test_all_qualities_and_unknown_quality_are_stored_without_quantity(self):
        for quality in ('', '普通', '罕见', '稀有', '传奇', '不朽', '至宝', '天界'):
            item = classify(0, [quality + '级' if quality else '未知品质', '✦供奉材料'])
            item.count = None
            self.assertEqual((item.action, item.kind, item.material_type), ('store', 'material', 'offering'))
            engine = fake_engine()
            self.assertTrue(engine.action_allowed('store', item))
            self.assertFalse(engine.action_allowed('corrode', item))
            self.assertFalse(engine.action_allowed('synthesize', item))
            self.assertFalse(selected_for_corrosion(item, {'material': ['普通', '传奇', '至宝']}))

    def test_locked_offering_is_retained(self):
        for label in ('已锁定', '已鎖定', '解除锁定', '解除鎖定'):
            self.assertEqual(classify(0, ['供奉材料', label]).action, 'keep')

    def test_aliases_and_type_icon_are_supported(self):
        for label in ('0 供 奉 材 料', '✦供养材料', '供獻材料', '供献材料'):
            self.assertEqual(classify(0, [label]).action, 'store')
            self.assertTrue(has_item_details([label]))

    def test_partial_label_description_and_baseline_cannot_authorize_storage(self):
        for label in ('供奉', '可以用它供奉', '供奉材料类型未确认', '获得了供奉材料', '可用于供奉材料'):
            self.assertEqual(classify(0, ['普通级', '攻击伤害', label]).action, 'keep')
        item = classify(0, ['普通级', '供奉材料'], baseline=['供奉材料'])
        self.assertEqual(item.action, 'keep')
        engine = fake_engine()
        forged = replace(classify(0, ['普通级', '制作材料']), action='store')
        self.assertFalse(engine.action_allowed('store', forged))

    def test_streaming_offering_uses_storage_without_alt_or_stack_ocr(self):
        r = runner()
        item = replace(classify(0, ['供奉材料']), count=None)
        r.e.store_item = Mock(return_value=True)
        r.accept(item)
        r.e.store_item.assert_called_once_with(item, recognized=True)
        r.e.game.right_click.assert_not_called()
        r.e.read_stack_count.assert_not_called()
        self.assertEqual(r.stored, 1)
        self.assertFalse(r.batch)

    def test_storage_confirms_ctrl_transfer_for_unknown_quality_offering(self):
        helper = test_warehouse.WarehouseExecutionTest()
        engine, clock = helper.engine(test_warehouse.warehouse_frame(source=False, destination=True))
        item = classify(0, ['供奉材料'])
        engine.read_item.return_value = item
        with patch('engine.time.monotonic', side_effect=lambda: clock['now']):
            self.assertTrue(engine.store_item(item, recognized=True))
        engine.game.right_click.assert_called_once_with([100, 200], ctrl=True)
        engine.game.click.assert_not_called()
        engine.read_stack_count.assert_not_called()


if __name__ == '__main__':
    unittest.main()
