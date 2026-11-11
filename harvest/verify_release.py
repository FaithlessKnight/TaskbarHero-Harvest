"""Verify every ZIP member and smoke-test the extracted runtime without input."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT.parent / 'dist' / 'TaskbarHero-Harvest-1.49-TheFallen.zip'
SMOKE = '''
import json
from pathlib import Path
import sys
import tkinter as tk
sys.path.insert(0, str(Path.cwd()))
import app
from windows_io import OCR
from vision import asset
from process_diagnostics import game_processes, session_id
from game_paths import same_game_installation
root = tk.Tk()
root.withdraw()
app.dpi_aware()
instance = app.App(root)
root.update_idletasks()
assert root.title() == 'TaskbarHero 收菜助手 1.49'
def labels(widget):
    values = []
    for child in widget.winfo_children():
        if 'text' in child.keys():
            values.append(str(child.cget('text')))
        values.extend(labels(child))
    return values
assert '作者：TheFallen' in labels(root)
assert '多开诊断' in labels(root)
assert '新增规则' in labels(root)
assert '导入配置…' in labels(root)
assert [instance.book.tab(tab, 'text') for tab in instance.book.tabs()][-1] == '补充物品与配置'
assert instance.config['points'] == {}
assert instance.config['regions'] == {}
assert instance.config['learned'] == {}
assert instance.config['supplemental_items'] == []
assert not instance.auto_enabled()
assert asset('warehouse-page-1').width == 12
assert asset('warehouse-pages-add').width == 26
assert asset('material-inscription').width == 48
from tooltip_regions import item_regions
from window_restore import restore_positions
from tooltip_ocr import header_view, consensus
from vision import header_equipment_stats
assert callable(item_regions)
assert callable(restore_positions)
assert callable(header_view)
assert consensus([{'lines':['稀有级', '铭刻材料'], 'text':''}] * 2)['labels'] == ['铭刻材料']
assert callable(header_equipment_stats)
for name in ('tooltip-top-left', 'tooltip-top-right', 'rarity-uncommon-orb',
             'rarity-uncommon-scroll', 'rarity-transcendent-bow', 'rarity-transcendent-ingot',
             'rarity-rare-sap', 'material-engraving',
             'stat-cooldown-reduction', 'stat-attack-damage'):
    assert asset(name).width > 0, name
for name in ('settings-button', 'settings-graphics', 'settings-graphics-selected', 'settings-restore-row',
             'settings-restore-label', 'panel-settings', 'panel-close', 'panel-hero'):
    assert asset(name).width > 0, name
from rules import classify
from windows_io import Game, TargetOccluded
from window_isolation import GameIsolation, WindowPlacement
assert __import__('ctypes').sizeof(WindowPlacement) == 44
assert callable(GameIsolation.ensure)
assert issubclass(TargetOccluded, RuntimeError)
assert callable(Game.can_target)
assert classify(0, ['传奇级', '铭刻材料']).action == 'store'
assert classify(0, ['稀有级', '铭刻材料'], selection={}).action == 'corrode'
assert same_game_installation('C:/Sandbox/example-user/zw/drive/D/X86/STEAM/steamapps/common/TaskbarHero/TaskBarHero.exe',
                              'D:/X86/STEAM/steamapps/common/TaskbarHero/TaskBarHero.exe')
from unittest.mock import patch
import copy
import preferences_ui
from config_transfer import load_file, export_data, save_file
panel = instance.preferences
instance.config['supplemental_items'] = []
instance.config.update(window_size=[970, 892],
    points={'inventory_first': [100, 200], 'inventory_last': [340, 280]},
    regions={'mode_label': [10, 10, 40, 20]})
calibration = copy.deepcopy({k: instance.config.get(k) for k in ('window_size', 'points', 'regions', 'learned')})
with patch('preferences_ui.messagebox.showerror', side_effect=AssertionError), \
     patch('preferences_ui.messagebox.showinfo'), \
     patch('preferences_ui.messagebox.askyesno', return_value=True):
    for name in ('测试银锭', '测试铜锭', '测试金锭'):
        panel.name.set(name)
        panel.add()
    assert len(instance.config['supplemental_items']) == 3
    assert len(json.loads(app.CONFIG.read_text(encoding='utf-8'))['supplemental_items']) == 3
    identifier = instance.config['supplemental_items'][0]['id']
    panel.tree.selection_set(identifier)
    panel.edit()
    panel.action.set('放入仓库')
    panel.all_qualities.set(False)
    panel.qualities['稀有'].set(True)
    panel.update()
    assert instance.config['supplemental_items'][0]['action'] == 'store'
    assert instance.config['supplemental_items'][0]['qualities'] == ['稀有']
    item = classify(0, ['测试银锭', '稀有级', '制作材料'])
    instance.render_items([item])
    instance.tree.selection_set('0')
    panel.from_record()
    assert panel.name.get() == '测试银锭'
    assert not panel.all_qualities.get()
    assert panel.qualities['稀有'].get()
    assert instance.book.select() == str(panel.frame)
    panel.tree.selection_set(identifier)
    panel.delete()
    assert len(instance.config['supplemental_items']) == 2
    panel.select_all()
    panel.delete()
    assert instance.config['supplemental_items'] == []
    panel.name.set('测试秘银')
    panel.all_qualities.set(True)
    panel.add()
    panel.include_items.set(True)
    panel.include_calibration.set(False)
    export_path = Path.cwd() / 'smoke-preferences.json'
    with patch('preferences_ui.filedialog.asksaveasfilename', return_value=str(export_path)):
        panel.export_config()
    exported = load_file(export_path)
    assert 'calibration' not in exported
    assert len(exported['supplemental_items']) == 1
    saved_rules = copy.deepcopy(instance.config['supplemental_items'])
    panel.select_all()
    panel.delete()
    with patch('preferences_ui.filedialog.askopenfilename', return_value=str(export_path)):
        panel.import_config()
    assert instance.config['supplemental_items'] == saved_rules
    assert all(instance.config[k] == v for k, v in calibration.items())
    assert not instance.auto_enabled()
    panel.include_items.set(False)
    instance.hover_seconds.set(1.7)
    with patch('preferences_ui.filedialog.askopenfilename', return_value=str(export_path)):
        panel.import_config()
    assert instance.hover_seconds.get() == exported['settings']['hover_seconds']
    assert instance.config['supplemental_items'] == saved_rules
    full_path = Path.cwd() / 'smoke-calibration.json'
    save_file(full_path, export_data(instance.config, include_calibration=True, include_items=True))
    panel.include_calibration.set(True)
    instance.config['points'] = {}
    with patch('preferences_ui.filedialog.askopenfilename', return_value=str(full_path)):
        panel.import_config()
    assert instance.config['points'] == calibration['points']
    instance.busy = True
    instance.refresh_controls()
    panel.name.set('不应新增')
    panel.add()
    assert instance.config['supplemental_items'] == saved_rules
    assert all(str(w.cget('state')) == 'disabled' for w, _ in panel.controls)
    instance.busy = False
    instance.auto_harvest.start(30)
    instance.refresh_controls()
    assert all(str(w.cget('state')) == 'disabled' for w, _ in panel.controls)
    with patch('preferences_ui.filedialog.askopenfilename', side_effect=AssertionError):
        panel.import_config()
    instance.auto_harvest.cancel()
    instance.refresh_controls()
    root.update_idletasks()
from dataclasses import replace
from unittest.mock import Mock
from engine import Engine
from streaming import StreamRunner
test_engine = Engine.__new__(Engine)
test_engine.config = copy.deepcopy(instance.config)
test_engine.config['corrosion_selection'] = {'equipment': [], 'material': ['稀有']}
test_engine.game = Mock()
test_engine.note = Mock()
test_engine.neutral = Mock()
test_engine.inventory_point = Mock(return_value=[100, 200])
test_engine.read_source_count = Mock(return_value=5)
test_board = []
test_engine.game.right_click.side_effect = lambda *args, **kwargs: test_board.append(len(test_board))
test_engine.occupied = Mock(side_effect=lambda mode: list(test_board))
test_engine.settled_board = Mock(side_effect=lambda *args: (test_engine.game.frame(), list(test_board)))
test_runner = StreamRunner(test_engine)
test_item = replace(classify(0, ['测试银锭', '稀有级', '制作材料']), count=5)
test_runner.accept(test_item)
assert test_engine.game.right_click.call_count == 5
assert [i.count_location for i in test_runner.batch] == ['inventory_units'] * 5
assert [i.source_count for i in test_runner.batch] == [5] * 5
assert all(call.kwargs == {'alt': True} for call in test_engine.game.right_click.call_args_list)
test_board.clear()
test_engine.read_source_count.reset_mock()
test_engine.read_source_count.return_value = 0
assert test_engine.wait_corrode_progress(test_runner.batch, [1] * 5, fast=True) == ([0] * 5, [1] * 5)
test_engine.read_source_count.assert_called_once()
test_engine.game.click.assert_not_called()
test_engine.game.right_click.reset_mock()
test_engine.read_source_count.reset_mock()
test_engine.read_source_count.side_effect = AssertionError('unknown count does not need rechecking')
test_runner = StreamRunner(test_engine)
test_item = replace(test_item, count=None)
test_runner.accept(test_item)
test_engine.game.right_click.assert_called_once_with([100, 200], alt=True)
assert len(test_runner.batch) == 1
assert test_runner.batch[0].count_location == 'single_unconfirmed'
test_board.clear()
assert test_engine.wait_corrode_progress(test_runner.batch, [1], fast=True) == ([0], [1])
test_engine.read_source_count.assert_not_called()
from supplemental import resolved_readings, new_rule, title_view
test_rules = [new_rule('黑钢锭', 'material', 'store')]
assert resolved_readings([['黑锭'] * 2, ['黑钢锭'] * 2], test_rules) == ['黑钢锭'] * 2
assert callable(title_view)
from windows_io import Frame
from vision import header_qualities, header_material_types
sap_pixels = bytearray(bytes([0, 0, 0, 255]) * 234 * 180)
for sap_name, sap_x, sap_y in (('rarity-rare-sap', 63, 49), ('material-engraving', 75, 67)):
    sap_glyph = asset(sap_name)
    for sap_row in range(sap_glyph.height):
        sap_start = ((sap_y + sap_row) * 234 + sap_x) * 4
        sap_pixels[sap_start:sap_start + sap_glyph.width * 4] = sap_glyph.pixels[
            sap_row * sap_glyph.width * 4:(sap_row + 1) * sap_glyph.width * 4]
sap_frame = Frame(234, 180, bytes(sap_pixels))
assert header_qualities(sap_frame) == ['稀有']
assert header_material_types(sap_frame) == ['雕刻材料']
sap_item = classify(0, header_material_types(sap_frame) + ['古树树液', '怖有'],
                    quality_evidence=header_qualities(sap_frame))
assert (sap_item.action, sap_item.kind, sap_item.material_type) == ('corrode', 'material', 'engraving')
ocr = OCR()
ocr.close()
processes = game_processes()
assert session_id(__import__('os').getpid()) is not None
instance.close()
print(json.dumps({'runtime':sys.executable, 'gui':'ok', 'author':'TheFallen',
                  'calibration':'ok', 'windows_ocr':'ok', 'auto_harvest':'off',
                  'supplemental_ui':'add_edit_delete_single_multi_all_ok', 'config_transfer':'ok',
                  'counted_stack_shortcuts':'5_inputs_5_consumed_ok',
                  'unknown_quantity':'one_alt_right_click_ok', 'title_recovery':'ok',
                  'ancient_tree_sap':'rare_engraving_material_ok',
                  'process_snapshot':'ok', 'local_game_process_count':len(processes)}, ensure_ascii=False))
'''


def verify():
    with zipfile.ZipFile(ARCHIVE) as archive:
        manifest = archive.read('SHA256SUMS.txt').decode('utf-8').splitlines()
        for line in manifest:
            digest, name = line.split('  ', 1)
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise RuntimeError('文件校验失败：' + name)
        with tempfile.TemporaryDirectory(prefix='TaskbarHero-release-') as folder:
            base = Path(folder).resolve()
            for name in archive.namelist():
                if not (base / name).resolve().is_relative_to(base):
                    raise RuntimeError('ZIP 路径异常：' + name)
            archive.extractall(base)
            helper = base / 'harvest'
            runtime = helper / 'runtime' / 'python.exe'
            result = subprocess.run([str(runtime), '-I', '-B', '-X', 'utf8', '-c', SMOKE],
                                    cwd=helper, text=True, encoding='utf-8', capture_output=True,
                                    timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
            print(result.stdout, end='')
            if result.returncode:
                raise RuntimeError(result.stderr)
    print(json.dumps({'verified_files': len(manifest), 'zip': str(ARCHIVE)}, ensure_ascii=False))


if __name__ == '__main__':
    verify()
