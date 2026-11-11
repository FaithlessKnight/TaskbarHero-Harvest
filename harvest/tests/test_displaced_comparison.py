"""Displaced equipment tooltip with Lv overlay and another quality beside it."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_displaced_comparison import comparison_pair
from check_regressions_140 import place
from engine import Engine
from rules import classify
from tooltip_regions import item_regions, icon_matches
from vision import find_matches, header_equipment_stats, header_qualities, asset
from windows_io import Frame
from test_engine import fake_engine
from test_panels import fill_region


SOURCE = [431, 322, 32, 32]
BOW = [466, 537, 226, 137]
COMPARISON = [694, 449, 226, 225]


class DisplacedEquipmentTest(unittest.TestCase):
    def test_real_level_overlay_reproduces_old_mismatch_and_two_patches_recover(self):
        before, after, point = comparison_pair()
        old_core = before.crop([437, 327, 19, 16])
        self.assertFalse(find_matches(after, old_core, [476, 573, 48, 54], mean_limit=8, changed_limit=.06))
        diagnostics = {}
        self.assertEqual(item_regions(after, before, point, 40, SOURCE, diagnostics=diagnostics), [BOW])
        selected = [candidate for candidate in diagnostics['candidates'] if 'matched_by' in candidate]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['matched_by'], 'two-upper-patches')
        self.assertFalse(icon_matches(after, before, COMPARISON, SOURCE))

    def test_other_backpack_icons_cannot_claim_the_bow_or_equipped_scepter(self):
        before, after, point = comparison_pair()
        for col in (0, 1, 3, 4, 5, 6):
            point = [367 + 40 * col, 338]
            with self.subTest(col=col):
                self.assertEqual(item_regions(after, before, point, 40,
                                             [point[0] - 16, 322, 32, 32]), [])

    def test_only_one_matching_patch_does_not_authorize_far_tooltip(self):
        before, after, point = comparison_pair()
        altered = fill_region(after, [499, 590, 12, 12], (10, 10, 10))
        self.assertEqual(item_regions(altered, before, point, 40, SOURCE), [])

    def test_two_patches_at_different_offsets_do_not_authorize_far_tooltip(self):
        before, after, point = comparison_pair()
        right = after.crop([499, 590, 12, 12])
        altered = fill_region(after, [499, 590, 12, 12], (10, 10, 10))
        altered = place(altered, right, 503, 590)
        self.assertEqual(item_regions(altered, before, point, 40, SOURCE), [])

    def test_changed_source_and_static_old_tooltip_are_rejected(self):
        before, after, point = comparison_pair()
        altered = fill_region(after, [435, 326, 12, 12], (10, 10, 10))
        self.assertEqual(item_regions(altered, before, point, 40, SOURCE), [])
        self.assertEqual(item_regions(after, after, point, 40, SOURCE), [])

    def test_identical_sprite_in_two_far_panels_remains_ambiguous(self):
        before, after, point = comparison_pair()
        icon = after.crop([480, 583, 40, 40])
        altered = place(after, icon, 708, 495)
        self.assertTrue(icon_matches(altered, before, BOW, SOURCE))
        self.assertTrue(icon_matches(altered, before, COMPARISON, SOURCE))
        diagnostics = {}
        self.assertEqual(item_regions(altered, before, point, 40, SOURCE, diagnostics=diagnostics), [])
        self.assertEqual(diagnostics['method'], 'ambiguous-source-icons')

    def test_attack_label_inside_header_identifies_equipment_without_reading_damage(self):
        before, after, point = comparison_pair()
        frame = after.crop(BOW).padded()
        self.assertIn('攻击伤害', header_equipment_stats(frame))
        item = classify(16, ['永恒之弓', '稀有级', '攻击伤書4'] + header_equipment_stats(frame),
                        quality_evidence=header_qualities(frame))
        self.assertEqual((item.action, item.quality, item.kind), ('corrode', '稀有', 'equipment'))
        for extra in (['已锁定'], ['超凡级']):
            self.assertEqual(classify(16, ['稀有级'] + extra + header_equipment_stats(frame),
                                     quality_evidence=header_qualities(frame)).action, 'keep')

    def test_attack_label_in_effect_description_is_not_equipment_evidence(self):
        frame = Frame(234, 300, bytes([0, 0, 0, 255]) * 234 * 300)
        lower = place(frame, asset('stat-attack-damage'), 60, 160)
        self.assertEqual(header_equipment_stats(lower), [])

    def test_local_engine_reads_only_selected_bow_and_saves_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            before, after, point = comparison_pair()
            e = fake_engine()
            e.baseline_frame = before
            e.config['points'].update(inventory_first=[367, 258], inventory_last=[607, 338])
            e.game.frame.return_value = after
            # Whole-tooltip OCR cannot read the attack stat in this sample.
            value = {'text': '永恒之弓\n稀有级\n攻击伤書4', 'lines': ['永恒之弓', '稀有级', '攻击伤書4']}
            e.read_saved = Mock(side_effect=[({'text': '', 'lines': []}, 'before.bmp'),
                (value.copy(), str(Path(folder) / 'item.bmp')), (value.copy(), 'second.bmp'),
                ({'text': '', 'lines': []}, 'before2.bmp')])
            e.enhance_header = Mock(side_effect=AssertionError('native stat label should avoid extra OCR'))
            item = e.local_item(16, 'inventory', point)
            self.assertEqual((item.action, item.quality, item.kind), ('corrode', '稀有', 'equipment'))
            data = json.loads((Path(folder) / 'item.json').read_text(encoding='utf-8'))
            self.assertEqual(data['selected'], BOW)
            self.assertEqual(data['tooltip_selection']['method'], 'unique-source-icon')
            self.assertNotIn('超凡', item.text)
            e.game.right_click.assert_not_called()

    def test_unmatched_frame_and_reason_are_saved_without_ocr_or_clicks(self):
        with tempfile.TemporaryDirectory() as folder:
            before, after, point = comparison_pair()
            e = fake_engine()
            e.folder, e.serial, e.baseline_frame = Path(folder), 0, before
            e.config['points'].update(inventory_first=[367, 258], inventory_last=[607, 338])
            e.game.frame.return_value = after
            e.read_saved = Mock(side_effect=AssertionError('no confirmed region'))
            self.assertIsNone(e.local_item(15, 'inventory', [407, 338]))
            files = list(Path(folder).glob('*tooltip-unmatched.bmp'))
            self.assertEqual(len(files), 1)
            data = json.loads(files[0].with_suffix('.json').read_text(encoding='utf-8'))
            self.assertEqual(data['method'], 'source-icon-unconfirmed')
            self.assertEqual(len(data['candidates']), 2)
            e.game.right_click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
