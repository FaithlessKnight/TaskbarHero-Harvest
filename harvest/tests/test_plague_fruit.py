"""Named corrosion exception and the real tooltip whose OCR name was wrong."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rules import classify, has_item_details, QUALITY_NAMES, OTHER_QUALITIES
from vision import header_names, name_mask
from windows_io import Frame
from test_engine import fake_engine
from test_streaming import runner


class PlagueFruitTest(unittest.TestCase):
    def test_every_quality_bypasses_checkboxes_and_high_quality_storage(self):
        e = fake_engine()
        e.config['corrosion_selection'] = {'equipment': [], 'material': []}
        for quality in QUALITY_NAMES + OTHER_QUALITIES + ('',):
            with self.subTest(quality=quality):
                item = classify(0, ['瘟疫之果', quality + '级' if quality else '', '可交易'],
                                selection=e.config['corrosion_selection'])
                self.assertEqual((item.action, item.kind, item.material_type), ('corrode', 'material', 'plague_fruit'))
                self.assertTrue(e.action_allowed('corrode', item))
                self.assertFalse(e.action_allowed('store', item))
                self.assertFalse(e.action_allowed('synthesize', item))

    def test_conflicting_or_missing_quality_does_not_veto_confirmed_name(self):
        for qualities in ([], ['至宝级', '普通级']):
            self.assertEqual(classify(0, ['瘟 疫 之 果'] + qualities).action, 'corrode')
        self.assertTrue(has_item_details(['瘟 疫 之 果', '可交易']))

    def test_locked_fruit_remains_untouched(self):
        item = classify(0, ['瘟疫之果', '宇宙级', '已锁定'])
        self.assertEqual(item.action, 'keep')
        self.assertFalse(fake_engine().action_allowed('corrode', item))

    def test_name_mentioned_in_description_is_not_an_exception(self):
        for lines in (['别的材料', '天界级', '可获得瘟疫之果'],
                      ['别的材料', '天界级', '瘟疫之果'],
                      ['别的材料', '天界级'] + ['属性'] * 5 + ['瘟疫之果']):
            self.assertEqual(classify(0, lines).action, 'store')
        self.assertEqual(classify(0, ['果核', '稀有级', '可制作瘟疫之果'],
                                  selection={'equipment': [], 'material': []}).action, 'keep')

    def test_real_name_image_is_recognized_despite_bad_ocr(self):
        frame = Frame.load_bmp(Path(__file__).parent / 'fixtures/plague-fruit.bmp')
        self.assertEqual(header_names(frame), ['瘟疫之果'])
        self.assertEqual(header_names(frame.padded()), ['瘟疫之果'])

    def test_name_strokes_survive_different_rarity_colors(self):
        source = Frame.load_bmp(Path(__file__).parent / 'fixtures/plague-fruit.bmp')
        mask = name_mask(source)
        for color in ((140, 140, 140), (20, 180, 250), (200, 40, 210)):
            pixels = bytearray(source.pixels)
            for y in range(16, 32):
                for x in range(91, 147):
                    n = (y * source.width + x) * 4
                    if mask.pixels[n]:
                        pixels[n:n + 3] = bytes(color)
            self.assertEqual(header_names(Frame(source.width, source.height, bytes(pixels))), ['瘟疫之果'])

    def test_real_image_reaches_local_classifier_with_no_recognized_name_or_rarity(self):
        frame = Frame.load_bmp(Path(__file__).parent / 'fixtures/plague-fruit.bmp')
        e = fake_engine()
        e.baseline_frame = Frame(frame.width, frame.height, bytes([40, 40, 40, 255]) * frame.width * frame.height)
        e.game.frame.return_value.tooltip_boxes.return_value = [[0, 0, frame.width, frame.height]]
        e.game.frame.return_value.crop.return_value = frame
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'tooltip.bmp')
            bad = {'lines': ['瘟 疳 之 果', '稀 与 龌', '可交易'], 'text': '瘟 疳 之 果 稀 与 龌 可交易'}
            e.read_saved = Mock(side_effect=[({'lines': [], 'text': ''}, path), (bad, path),
                                             (bad, path), ({'lines': [], 'text': ''}, path)])
            item = e.local_item(0, 'inventory', [100, 200])
        self.assertEqual((item.action, item.material_type), ('corrode', 'plague_fruit'))
        self.assertTrue(e.action_allowed('corrode', item))
        e.game.right_click.assert_not_called()

    def test_high_quality_fruit_enters_cube_instead_of_warehouse(self):
        r = runner()
        item = classify(0, ['瘟疫之果', '天界级'])
        r.e.read_source_count.return_value = item.count
        r.accept(item)
        r.e.game.right_click.assert_called_once_with([100, 200], alt=True)
        self.assertEqual(len(r.batch), 1)


if __name__ == '__main__':
    unittest.main()
