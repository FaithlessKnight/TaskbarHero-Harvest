import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App, DEFAULTS
from config_transfer import export_data, merge_config, load_file, save_file, validate_data, MAX_BYTES
from supplemental import new_rule
from engine import grid, cell_box
from windows_io import Frame


def configured(name='银锭'):
    c = copy.deepcopy(DEFAULTS)
    c.update(window_size=[970, 892], points={'inventory_first': [100, 200],
        'inventory_last': [340, 280]}, regions={'mode_label': [10, 10, 40, 20]},
        supplemental_items=[new_rule(name, 'material', 'store')])
    return c



def calibrated():
    """Deterministic image templates; never read a user's calibration file."""
    c = configured()
    c['points'].update(cube_first=[400, 200], cube_last=[480, 280],
                       warehouse_first=[600, 200], warehouse_last=[840, 440],
                       warehouse_empty=[600, 200])
    c['regions'].update(options_corrode=[10, 40, 100, 20],
                        options_synthesize=[10, 70, 100, 20],
                        recipe_synthesize=[10, 100, 100, 20])

    def image(width, height):
        return Frame(width, height, bytes([10, 20, 30, 255]) * width * height).template()

    for mode in ('corrode', 'synthesize'):
        record = {'empty_board': [image(*cell_box(c, 'cube', p)[2:])
                                  for p in grid(c, 'cube', 3, 3)]}
        keys = ['mode_label', 'options_' + mode]
        if mode == 'synthesize':
            keys.append('recipe_synthesize')
            record['empty_warehouse'] = image(*cell_box(c, 'warehouse', c['points']['warehouse_empty'])[2:])
        record.update({key: image(*c['regions'][key][2:]) for key in keys})
        c['learned'][mode] = record
    return c


class ConfigTransferTest(unittest.TestCase):
    def test_four_export_combinations_contain_only_requested_sections(self):
        original = configured()
        snapshot = copy.deepcopy(original)
        for cal in (False, True):
            for items in (False, True):
                with self.subTest(cal=cal, items=items):
                    data = export_data(original, include_calibration=cal, include_items=items)
                    self.assertEqual('calibration' in data, cal)
                    self.assertEqual('supplemental_items' in data, items)
                    self.assertNotIn('bag_columns', data['settings'])
                    self.assertNotIn('window_size', data['settings'])
                    self.assertNotIn('learned', data['settings'])
                    self.assertEqual(validate_data(data), data)
        self.assertEqual(original, snapshot)

    def test_import_checkbox_matrix_preserves_unselected_sections(self):
        original = configured('原物品')
        incoming = configured('新物品')
        incoming.update(window_size=[1200, 900], bag_columns=8, hover_seconds=2)
        data = export_data(incoming, include_calibration=True, include_items=True)
        snapshot = copy.deepcopy(original)
        for cal in (False, True):
            for items in (False, True):
                got = merge_config(original, data, include_calibration=cal, include_items=items)
                self.assertEqual(got['hover_seconds'], 2)
                self.assertEqual(got['bag_columns'], 8 if cal else 7)
                self.assertEqual(got['window_size'], incoming['window_size'] if cal else original['window_size'])
                self.assertEqual(got['supplemental_items'], incoming['supplemental_items'] if items else original['supplemental_items'])
        self.assertEqual(original, snapshot)

    def test_missing_sections_preserve_existing_even_when_checked(self):
        c = configured()
        got = merge_config(c, export_data(DEFAULTS), include_calibration=True, include_items=True)
        for key in ('points', 'regions', 'window_size', 'supplemental_items', 'bag_columns'):
            self.assertEqual(got[key], c[key])

    def test_empty_selected_items_explicitly_clears_rules(self):
        got = merge_config(configured(), export_data(DEFAULTS, include_items=True), include_items=True)
        self.assertEqual(got['supplemental_items'], [])

    def test_new_uncalibrated_install_can_export_and_import(self):
        data = export_data(DEFAULTS, include_calibration=True, include_items=True)
        self.assertIsNone(data['calibration']['window_size'])
        got = merge_config(configured(), data, include_calibration=True)
        self.assertEqual(got['points'], {})
        self.assertEqual(got['learned'], {})

    def test_synthetic_calibration_can_roundtrip_including_templates(self):
        c = calibrated()
        data = export_data(c, include_calibration=True)
        got = merge_config(DEFAULTS, data, include_calibration=True)
        for key in ('points', 'regions', 'learned', 'window_size'):
            self.assertEqual(got[key], c[key])

    def test_nonfinite_boolean_out_of_range_and_unknown_settings_fail(self):
        data = export_data(DEFAULTS)
        for key, value in (('hover_seconds', float('nan')), ('hover_seconds', float('inf')),
                           ('hover_seconds', True), ('hover_seconds', .1), ('action_interval_seconds', .1),
                           ('max_batches', 1.5), ('auto_interval_minutes', 0), ('max_batches', 100000),
                           ('max_batches', 10 ** 1000)):
            bad = copy.deepcopy(data)
            bad['settings'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_data(bad)
        for update in ({'format': 'other'}, {'version': 2}, {'version': True}, {'run_command': 'bad'}):
            with self.assertRaises(ValueError):
                validate_data(dict(data, **update))

    def test_bad_coordinates_and_grids_are_rejected_before_any_mutation(self):
        c = configured()
        snapshot = copy.deepcopy(c)
        data = export_data(c, include_calibration=True)
        for key, value in (('window_size', [0, 900]), ('window_size', [1920.5, 900]),
                           ('bag_columns', 1), ('bag_rows', 4), ('bag_total_rows', 2),
                           ('points', {'inventory_first': [9999, 100]}),
                           ('points', {'inventory_first': [-1, 100]}),
                           ('regions', {'mode_label': [900, 10, 100, 20]}),
                           ('regions', {'mode_label': [10, 10, 0, 20]})):
            bad = copy.deepcopy(data)
            bad['calibration'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                merge_config(c, bad, include_calibration=True)
            self.assertEqual(c, snapshot)

    def test_invalid_optional_section_fails_even_if_not_selected(self):
        c = configured()
        data = export_data(c, include_items=True)
        data['supplemental_items'][0]['action'] = 'execute'
        with self.assertRaises(ValueError):
            merge_config(c, data)

    def test_corrupted_learned_pixels_dimensions_missing_fields(self):
        c = calibrated()
        data = export_data(c, include_calibration=True)
        for field, value in (('pixels', '*bad*'), ('pixels', 'AAAA'), ('width', 0), ('height', 4097)):
            bad = copy.deepcopy(data)
            bad['calibration']['learned']['corrode']['mode_label'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_data(bad)
        bad = copy.deepcopy(data)
        bad['calibration']['learned']['corrode']['empty_board'].pop()
        with self.assertRaises(ValueError):
            validate_data(bad)
        bad = copy.deepcopy(data)
        bad['calibration']['learned']['corrode']['mode_label'] = Frame(1, 1, bytes(4)).template()
        with self.assertRaises(ValueError):
            validate_data(bad)

    def test_json_file_roundtrip_duplicate_keys_and_oversize(self):
        data = export_data(configured(), include_calibration=True, include_items=True)
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'config.json'
            save_file(file, data)
            self.assertEqual(load_file(file), data)
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])
            for text in ('{"format":"a","format":"b"}', '{"hover_seconds":NaN}', '[broken'):
                file.write_text(text, encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_file(file)
            file.write_bytes(b' ' * (MAX_BYTES + 1))
            with self.assertRaises(ValueError):
                load_file(file)

    def test_failed_export_does_not_replace_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'settings.json'
            file.write_text('old', encoding='utf-8')
            with self.assertRaises(ValueError):
                save_file(file, {'version': 99})
            self.assertEqual(file.read_text(encoding='utf-8'), 'old')

    def test_loading_tk_vars_suppresses_autosave_and_never_starts_auto(self):
        a = App.__new__(App)
        a.config = configured()
        a.setting_vars = {key: Mock() for key in ('bag_columns', 'bag_rows', 'max_batches')}
        a.hover_seconds = Mock()
        a.action_interval = Mock()
        a.auto_minutes = Mock()
        a.auto_harvest = Mock()
        a.write_config = Mock()
        a.corrosion_vars = {'equipment': {'普通': Mock(), '传奇': Mock()}}
        a.config['corrosion_selection'] = {'equipment': ['普通'], 'material': []}
        a.auto_minutes.set.side_effect = lambda unused: a.auto_interval_changed()
        a.reload_settings()
        a.write_config.assert_not_called()
        a.auto_harvest.start.assert_not_called()
        a.corrosion_vars['equipment']['普通'].set.assert_called_once_with(True)
        a.corrosion_vars['equipment']['传奇'].set.assert_called_once_with(False)
        self.assertFalse(a._loading_settings)


if __name__ == '__main__':
    unittest.main()
