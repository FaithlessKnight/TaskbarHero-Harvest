"""Small-text recovery must retain local scope and field conflict protection."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_regressions_140 import pair, CASES
from engine import Engine
from tooltip_ocr import header_view, consensus
from windows_io import Frame, OCR
from test_engine import fake_engine


def result(*lines):
    return {'lines': list(lines), 'text': '\n'.join(lines)}


class HeaderEvidenceTest(unittest.TestCase):
    def test_two_complete_reads_required_and_unrelated_descriptions_rejected(self):
        agreed = consensus([result('稀有级', '铭刻材料'), result('稀有級', '銘刻材料')])
        self.assertEqual(agreed['qualities'], ['稀有'])
        self.assertEqual(agreed['labels'], ['铭刻材料'])
        for lines in (('稀有级', '可用于铭刻材料'), ('稀有铭文卷轴', '材料'), ('刻材料', '铭刻材料效果')):
            got = consensus([result(*lines), result(*lines)])
            self.assertEqual(got['labels'], [])
        got = consensus([result('稀有级', '铭刻材料'), result('刻材料')])
        self.assertEqual((got['qualities'], got['labels']), ([], []))

    def test_conflicts_locks_comparisons_and_uncertain_offerings_block(self):
        for first, second in ((result('稀有级', '已锁定'), result('稀有级')),
                              (result('稀有级', '已装备'), result('稀有级')),
                              (result('供奉材料'), result('铭刻材料')),
                              (result('供奉材料'), result()),
                              (result('雕刻材料'), result('铭刻材料'))):
            self.assertTrue(consensus([first, second])['blocked'])
        conflict = consensus([result('稀有级'), result('超凡级')])
        self.assertEqual(conflict['qualities'], [])
        self.assertEqual(conflict['observed_qualities'], ['稀有', '超凡'])

    def test_header_crop_excludes_title_and_long_effect_list(self):
        frame = Frame(234, 1200, bytes([0, 0, 0, 255]) * 234 * 1200)
        view = header_view(frame)
        self.assertLess(view.height, 100)
        self.assertLess(view.width, 210)
        self.assertIsNone(header_view(Frame(80, 30, bytes([0, 0, 0, 255]) * 80 * 30)))


class EngineHeaderRecoveryTest(unittest.TestCase):
    def setup_engine(self, folder):
        e = fake_engine()
        before, after, point = pair('rare-scroll')
        e.config['points'].update(inventory_first=point, inventory_last=[585, 260])
        e.baseline_frame = before
        e.game.frame.return_value = after
        e.rarity_lines = Mock(return_value=[])
        e.read_saved = Mock(side_effect=[(result(), 'before.bmp'),
            (result('卷轴'), str(Path(folder) / 'item.bmp')), (result('卷轴'), 'second.bmp'),
            (result(), 'before2.bmp'), (result('稀有级', '铭刻材料'), 'header1.bmp'),
            (result('稀有级', '铭刻材料'), 'header2.bmp')])
        return e, point

    def test_missing_quality_and_type_recovered_without_extra_hover(self):
        with tempfile.TemporaryDirectory() as folder:
            e, point = self.setup_engine(folder)
            with patch('engine.header_qualities', return_value=[]), patch('engine.header_material_types', return_value=[]):
                item = e.local_item(0, 'inventory', point)
            self.assertEqual((item.action, item.quality, item.material_type), ('corrode', '稀有', 'inscription'))
            e.game.hover.assert_not_called()
            calls = e.read_saved.call_args_list[-2:]
            self.assertEqual([c.kwargs for c in calls], [dict(scale=4, interpolation='cubic'),
                                                        dict(scale=5, interpolation='cubic')])
            data = json.loads((Path(folder) / 'item.json').read_text(encoding='utf-8'))
            self.assertEqual(data['frame_pixels'], [700, 700])
            self.assertEqual(data['header_enhancement']['labels'], ['铭刻材料'])

    def test_one_high_quality_read_blocks_earlier_corrosion_quality(self):
        with tempfile.TemporaryDirectory() as folder:
            e, point = self.setup_engine(folder)
            e.read_saved.side_effect = [(result(), 'before.bmp'),
                (result('稀有级'), str(Path(folder) / 'item.bmp')), (result('稀有级'), 'second.bmp'),
                (result(), 'before2.bmp'), (result('超凡级', '铭刻材料'), 'header1.bmp'),
                (result('稀有级', '铭刻材料'), 'header2.bmp')]
            with patch('engine.header_qualities', return_value=[]), patch('engine.header_material_types', return_value=[]):
                item = e.local_item(0, 'inventory', point)
            self.assertEqual(item.action, 'keep')
            self.assertIn('冲突', item.reason)

    def test_confirmed_supplied_items_do_not_add_ocr_work(self):
        for name in CASES:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                e, point = self.setup_engine(folder)
                before, after, point = pair(name)
                e.baseline_frame = before
                e.game.frame.return_value = after
                lines = ['攻击伤害1427'] if 'bow' in name else ['物品名称']
                e.read_saved.side_effect = [(result(), 'before.bmp'),
                    (result(*lines), str(Path(folder) / 'item.bmp')), (result(*lines), 'second.bmp'),
                    (result(), 'before2.bmp')]
                e.enhance_header = Mock(side_effect=AssertionError('confirmed item must remain fast'))
                e.local_item(0, 'inventory', point)
                e.enhance_header.assert_not_called()

    def test_long_tooltip_header_keeps_high_scale_within_ocr_size_cap(self):
        e = Engine.__new__(Engine)
        e.serial = 0
        e.ocr = Mock(max_size=2600)
        e.ocr.read.return_value = result()
        with tempfile.TemporaryDirectory() as folder:
            e.folder = Path(folder)
            frame = Frame(234, 1200, bytes([0, 0, 0, 255]) * 234 * 1200)
            e.enhance_header(frame, 'long')
        self.assertEqual([call.kwargs['scale'] for call in e.ocr.read.call_args_list], [4, 5])
        self.assertTrue(all(call.kwargs['interpolation'] == 'cubic' for call in e.ocr.read.call_args_list))

    def test_ocr_protocol_preserves_requested_interpolation(self):
        ocr = OCR.__new__(OCR)
        ocr.process = Mock()
        ocr._receive = Mock(return_value={'ok': True})
        ocr.read('sample.bmp', scale=5, interpolation='cubic')
        payload = json.loads(ocr.process.stdin.write.call_args.args[0])
        self.assertEqual((payload['scale'], payload['interpolation']), (5, 'cubic'))


if __name__ == '__main__':
    unittest.main()
