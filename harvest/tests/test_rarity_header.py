from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rules import classify
from vision import asset, header_qualities
from windows_io import Frame
from test_engine import fake_engine


class HighQualityHeaderTest(unittest.TestCase):
    def test_supplied_ultimate_material_and_celestial_soulstone_headers(self):
        for name, quality in (('ultimate-material-reference', '至宝'), ('celestial-soulstone-reference', '天界')):
            frame = asset(name)
            self.assertEqual(header_qualities(frame), [quality])
            self.assertEqual(classify(0, header_qualities(frame) + ['装饰材料']).action, 'store')

    def test_tooltip_ocr_missing_rarity_is_recovered_from_its_own_header(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = fake_engine()
            current = asset('ultimate-material-reference')
            blank = Frame(current.width, current.height, bytes([0, 0, 0, 255]) * current.width * current.height)
            engine.baseline_frame = blank
            engine.game.frame.return_value = Mock(spec=Frame)
            engine.game.frame.return_value.tooltip_boxes.return_value = [[0, 0, current.width, current.height]]
            engine.game.frame.return_value.crop.return_value = current
            engine.read_saved = Mock(side_effect=[
                ({'lines': [], 'text': ''}, 'before.bmp'),
                ({'lines': ['星光蓝宝石', '装饰材料'], 'text': '星光蓝宝石\n装饰材料'}, str(Path(folder) / 'tooltip.bmp')),
                ({'lines': ['星光蓝宝石', '装饰材料'], 'text': '星光蓝宝石\n装饰材料'}, 'second.bmp'),
                ({'lines': [], 'text': ''}, 'before2.bmp'),
            ])
            item = engine.local_item(0, 'inventory', [100, 200])
            self.assertEqual((item.action, item.quality, item.kind), ('store', '至宝', 'material'))

    def test_high_rarity_template_blocks_conflicting_low_quality_corrosion(self):
        frame = asset('ultimate-material-reference')
        item = classify(0, header_qualities(frame) + ['罕见级', '装饰材料'])
        self.assertEqual(item.action, 'keep')

    def test_blacksteel_supplied_and_actual_run_headers_confirm_transcendent(self):
        for name in ('blacksteel-ingot-1.47', 'blacksteel-local-1.47'):
            frame = Frame.load_bmp(Path(__file__).parent / 'fixtures' / (name + '.bmp'))
            if name == 'blacksteel-ingot-1.47':
                frame = frame.padded()
            self.assertEqual(header_qualities(frame), ['超凡'])
            self.assertEqual(classify(0, header_qualities(frame) + ['制作材料']).action, 'store')
            self.assertEqual(classify(0, header_qualities(frame) + ['稀有级', '制作材料']).action, 'keep')

    def test_transcendent_ingot_glyph_in_description_is_not_header_evidence(self):
        from check_regressions_140 import place
        frame = Frame(234, 240, bytes(234 * 240 * 4))
        frame = place(frame, asset('rarity-transcendent-ingot'), 64, 120)
        self.assertEqual(header_qualities(frame), [])

    def test_glyph_in_description_area_cannot_supply_quality(self):
        frame = asset('ultimate-material-reference')
        pixels = bytearray(bytes([0, 0, 0, 255]) * frame.width * frame.height)
        glyph = asset('rarity-ultimate')
        for row in range(glyph.height):
            n = ((row + 120) * frame.width + 65) * 4
            pixels[n:n + glyph.width * 4] = glyph.pixels[row * glyph.width * 4:(row + 1) * glyph.width * 4]
        self.assertEqual(header_qualities(Frame(frame.width, frame.height, bytes(pixels))), [])


if __name__ == '__main__':
    unittest.main()
