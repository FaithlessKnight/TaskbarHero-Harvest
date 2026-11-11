"""Read supplied tooltip screenshots without capturing the desktop or sending input."""
import argparse
import json

from rules import classify
from windows_io import OCR


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('offering')
    parser.add_argument('equipment')
    args = parser.parse_args()
    ocr = OCR()
    try:
        offering = ocr.read(args.offering, scale=3)
        equipment = ocr.read(args.equipment, scale=3)
        first = classify(0, offering['lines'], full_text=offering['text'])
        # This cropped tooltip has no icon level. It must be kept until the
        # inventory icon provides a level; a separate test covers grouping.
        second = classify(1, equipment['lines'], full_text=equipment['text'])
        assert first.action == 'store' and first.material_type == 'offering', first
        assert second.action == 'keep' and second.quality == '传奇' and second.kind == 'equipment', second
        print(json.dumps({'offering': first.to_dict(), 'equipment': second.to_dict()}, ensure_ascii=False, indent=2))
    finally:
        ocr.close()


if __name__ == '__main__':
    main()
