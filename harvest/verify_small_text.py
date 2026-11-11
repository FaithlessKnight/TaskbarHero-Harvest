"""Compare native and thresholded OCR on the supplied game's pixel text."""
from pathlib import Path

from windows_io import Frame, OCR, ROOT


def main():
    ocr = OCR()
    try:
        folder = ROOT / 'runs' / 'ocr-check'
        for name, language in (('sample-mode', 'zh'), ('sample-level', 'en')):
            frame = Frame.load_bmp(folder / (name + '.bmp'))
            if name == 'sample-level':
                frame = frame.crop([0, frame.height - 17, frame.width, 17])
            for threshold in (None, 90, 110, 150, 200, 230):
                image = frame if threshold is None else frame.binary(threshold)
                path = folder / f'{name}-{threshold}-processed.bmp'
                image.save(path)
                for scale in (2, 3, 4, 5):
                    result = ocr.read(path, scale, language)
                    print(name, threshold, scale, repr(result['text']))
    finally:
        ocr.close()


if __name__ == '__main__':
    main()
