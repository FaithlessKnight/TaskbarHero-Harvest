"""Selection affects classification and every stage before consumption."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from engine import config_hash
from rules import (classify, corrosion_selection, selected_for_corrosion,
                   SELECTABLE_QUALITIES, CORROSION_TYPES, MATERIAL_NAMES, has_item_details)
from test_engine import fake_engine


def sample(kind, quality, selection, level='90'):
    label = '防御力660' if kind == 'equipment' else MATERIAL_NAMES.get(kind, '制作材料')
    return classify(0, ['测试物品', quality + '级', label, '需要等级' + level if level else ''], selection=selection)


class SelectionRulesTest(unittest.TestCase):
    def test_each_of_12_boxes_selects_only_its_own_quality_and_type(self):
        for chosen_kind in CORROSION_TYPES:
            for chosen_quality in SELECTABLE_QUALITIES:
                selection = {chosen_kind: [chosen_quality]}
                for kind in CORROSION_TYPES:
                    for quality in SELECTABLE_QUALITIES:
                        with self.subTest(box=(chosen_kind, chosen_quality), item=(kind, quality)):
                            item = sample(kind, quality, selection)
                            chosen = (kind, quality) == (chosen_kind, chosen_quality) and quality != '至宝'
                            self.assertEqual(item.action == 'corrode', chosen)
                            self.assertEqual(selected_for_corrosion(item, selection), chosen)
                            if not chosen:
                                self.assertEqual(item.action, 'store' if quality == '至宝' else 'synthesize' if
                                                 kind == 'equipment' and quality in ('传奇', '不朽') else 'keep')

    def test_multiple_boxes_are_independent_and_round_trip_through_json(self):
        selection = {'equipment': ['普通', '至宝'], 'material': ['不朽', '稀有']}
        restored = json.loads(json.dumps(corrosion_selection(selection), ensure_ascii=False))
        self.assertEqual(restored, corrosion_selection(selection))
        for kind, quality in [('equipment', '至宝'), ('decoration', '不朽'), ('engraving', '稀有')]:
            self.assertEqual(sample(kind, quality, restored).action, 'store' if quality == '至宝' else 'corrode')
        self.assertEqual(sample('crafting', '普通', restored).action, 'keep')

    def test_defaults_preserve_previous_scope_and_do_not_enable_new_high_qualities(self):
        selection = corrosion_selection()
        self.assertEqual(selection['equipment'], ['普通', '罕见', '稀有'])
        self.assertEqual(selection['material'], ['普通', '罕见', '稀有', '传奇', '不朽'])
        selection['equipment'].clear()
        self.assertTrue(corrosion_selection()['equipment'])

    def test_explicit_empty_selection_is_not_reset_to_defaults(self):
        for empty in ({}, {k: [] for k in CORROSION_TYPES}):
            for kind in CORROSION_TYPES:
                for quality in SELECTABLE_QUALITIES:
                    self.assertNotEqual(sample(kind, quality, empty).action, 'corrode')

    def test_invalid_configuration_cannot_enable_corrosion(self):
        for invalid in ([], True, {'equipment': '普通'}, {'equipment': ['天界']},
                        {'equipment': [None]}, {'other': ['普通']}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                corrosion_selection(invalid)

    def test_all_checked_still_protects_offerings_locks_and_unknown_types(self):
        selection = {k: list(SELECTABLE_QUALITIES) for k in CORROSION_TYPES}
        for quality in SELECTABLE_QUALITIES:
            for label in ('供奉材料', '制作材料\n供奉', '制作材料\n已锁定',
                          '可用于雕刻材料', '制作材料\n装备槽'):
                with self.subTest(quality=quality, label=label):
                    item = classify(0, [quality + '级', *label.splitlines()], selection=selection)
                    self.assertEqual(item.action, 'store' if label == '供奉材料' or (quality == '至宝' and not any(
                        word in label for word in ('供奉', '已锁定'))) else 'keep')
        self.assertEqual(classify(0, ['天界级', '制作材料'], selection=selection).action, 'store')

    def test_type_labels_and_suffixless_ultimate_rarity(self):
        for kind, label in [('crafting', '✦製作材料'), ('decoration', '一 装 饰 材 料'),
                            ('decoration', '裝飾材料'), ('engraving', '雕 刻 材 料')]:
            item = classify(0, ['测试', '至 宝', label], selection={kind: ['至宝']})
            self.assertEqual((item.action, item.material_type), ('store', kind))
            self.assertTrue(has_item_details(['至宝', label]))

    def test_legendary_corrosion_does_not_require_a_synthesis_level(self):
        self.assertEqual(sample('equipment', '传奇', {'equipment': ['传奇']}, level='').action, 'corrode')
        self.assertEqual(sample('equipment', '传奇', {}, level='').action, 'synthesize')


class ExecutionSelectionTest(unittest.TestCase):
    def test_selected_legendary_is_not_overwritten_by_icon_level_read(self):
        engine = fake_engine()
        engine.config['corrosion_selection'] = {'equipment': ['传奇']}
        expected = sample('equipment', '传奇', engine.config['corrosion_selection'], level='')
        engine.local_item = Mock(return_value=expected)
        engine.read_frame = Mock(return_value=({'text': '90', 'lines': ['90']}, 'item.bmp'))
        current = engine.read_item(0, [])
        self.assertEqual(current.action, 'corrode')
        self.assertIsNone(current.level)
        engine.read_frame.assert_not_called()
        engine.save_state.assert_called_once_with('inventory-00')

    def test_changed_selection_stops_a_stale_plan_before_any_drag(self):
        for kind in CORROSION_TYPES:
            engine = fake_engine()
            engine.config['corrosion_selection'] = {}
            expected = sample(kind, '普通', {kind: ['普通']})
            with self.subTest(kind=kind), self.assertRaisesRegex(RuntimeError, '勾选'):
                engine.transfer('corrode', [expected])
            engine.game.drag.assert_not_called()
            engine.game.click.assert_not_called()

    def test_material_subtypes_share_one_selected_category(self):
        engine = fake_engine()
        engine.config['corrosion_selection'] = selection = {'material': ['稀有']}
        engine.read_item = Mock(return_value=sample('engraving', '稀有', selection))
        engine.verify_board('corrode', [sample('crafting', '稀有', selection)])
        engine.game.click.assert_not_called()

    def test_all_material_categories_retain_reference_stack_tracking(self):
        for kind in ('crafting', 'decoration', 'engraving'):
            engine = fake_engine()
            engine.config['corrosion_selection'] = selection = {'material': ['不朽']}
            expected = replace(sample(kind, '不朽', selection), count=5)
            engine.read_item = Mock(return_value=expected)
            engine.read_source_count.return_value = 5
            engine.occupied = Mock(return_value=[0])
            batch = [expected]
            engine.transfer('corrode', batch)
            self.assertEqual((batch[0].count, batch[0].count_location, batch[0].material_type),
                             (5, 'inventory', kind))

    def test_loop_filters_unchecked_items_from_stale_scan_and_synthesis_preview(self):
        engine = fake_engine()
        old = [sample(k, '传奇', {k: ['传奇']}) for k in CORROSION_TYPES]
        old += [sample('equipment', '传奇', {}) for _ in range(9)]
        engine.config.update(corrosion_selection={'equipment': ['传奇']}, max_batches=1)
        engine.mode, engine.transfer, engine.commit, engine.sort_inventory = Mock(), Mock(), Mock(), Mock()
        engine.scan = Mock(side_effect=[old, []])
        with patch('engine.sort_button', return_value=[422, 597]):
            engine.execute(old)
        engine.commit.assert_called_once()
        self.assertEqual(len(engine.commit.call_args.args[1]), 1)
        engine.mode.assert_called_once_with('corrode')

    def test_fast_stack_loop_still_confirms_each_click_and_honors_interval_floor(self):
        for interval, expected_delay in ((0.6, 0.6), (0.1, 0.5), (3, 3)):
            engine = fake_engine()
            engine.config.update(corrosion_selection={'material': ['不朽']}, action_interval_seconds=interval)
            engine.occupied = Mock(return_value=[0])
            engine.verify_board = Mock()
            engine.wait_corrode_progress = Mock(side_effect=[([1], [1]), ([0], [1])])
            batch = [replace(sample('engraving', '不朽', engine.config['corrosion_selection']), count=2)]
            engine.corrode_batch(batch)
            self.assertEqual(engine.game.click.call_count, 2)
            self.assertEqual(engine.wait_corrode_progress.call_count, 2)
            self.assertEqual([c.args[0] for c in engine.game.wait.call_args_list], [expected_delay] * 2)


class SelectionUITest(unittest.TestCase):
    def test_checkbox_change_keeps_execution_available_and_changes_config_hash(self):
        app = App.__new__(App)
        app.busy = False
        app.config = {'corrosion_selection': corrosion_selection()}
        before = config_hash(app.config)
        app.run_button, app.status = Mock(), Mock()
        app.setting_vars = {}
        app.hover_seconds = Mock(get=Mock(return_value=0.3))
        app.action_interval = Mock(get=Mock(return_value=0.8))
        app.corrosion_vars = {k: {q: Mock(get=Mock(return_value=(k, q) == ('material', '至宝')))
                                 for q in SELECTABLE_QUALITIES} for k in CORROSION_TYPES}
        app.selection_changed()
        app.run_button.configure.assert_called_once_with(state='normal')
        app.settings()
        self.assertEqual(app.config['corrosion_selection']['material'], ['至宝'])
        self.assertEqual(app.config['corrosion_selection']['equipment'], [])
        self.assertNotEqual(config_hash(app.config), before)


if __name__ == '__main__':
    unittest.main()
