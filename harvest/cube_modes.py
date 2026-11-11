"""Locate a requested function inside the cube dropdown from its image or OCR."""
from rules import compact
from vision import asset, find_matches
from windows_io import Frame


LABELS = {'corrode': '腐蚀', 'synthesize': '合成'}


def menu_region(config, frame):
    x, y, width, height = config['regions']['mode_label']
    points = config['points']
    menu = points['mode_menu']
    choices = [points['mode_' + mode] for mode in LABELS]
    left = max(0, min(x, *(p[0] for p in choices)) - width)
    right = min(frame.width, max(menu[0], *(p[0] for p in choices)) + width)
    # Exclude the currently selected header. Only dropdown rows beneath it
    # can supply the requested function name, never the rest of the screen.
    top = max(0, y + height, menu[1] + height // 2)
    bottom = min(frame.height, max(p[1] for p in choices) + height * 8)
    if right <= left or bottom <= top:
        raise RuntimeError('魔方功能菜单区域超出窗口，请检查功能下拉按钮校准。')
    return [left, top, right - left, bottom - top]


def text_mask(frame):
    # The menu's neutral text is dim, while its brown/gold background is
    # strongly chromatic. Keep the full text silhouette, including gaps,
    # so a hovered row can change its background without changing its name.
    pixels = bytearray(frame.width * frame.height * 4)
    for n in range(0, len(pixels), 4):
        channels = frame.pixels[n:n + 3]
        ink = min(channels) >= 12 and max(channels) <= min(channels) * 3
        pixels[n:n + 4] = bytes([255 if ink else 0] * 3 + [255])
    return Frame(frame.width, frame.height, bytes(pixels))


def image_options(frame, wanted):
    reference = asset('cube-menu-' + wanted)
    positions = find_matches(frame, reference, mean_limit=4, changed_limit=0.025)
    if not positions:
        positions = find_matches(text_mask(frame), text_mask(reference),
                                 mean_limit=3, changed_limit=0.012)
    return [[x + reference.width // 2, y + reference.height // 2] for x, y in positions]


def unique_option(engine, wanted, points):
    if len(points) == 1:
        return points[0]
    if len(points) > 1:
        path = engine.save_state('mode-menu-ambiguous')
        raise RuntimeError('功能菜单中找到多处“' + LABELS[wanted] + '”，未选择。截图：' + path)
    return None


def option(engine, wanted, frame, *, scales=(3, 4)):
    region = menu_region(engine.config, frame)
    x, y, width, height = region
    points = [[x + px, y + py] for px, py in image_options(frame.crop(region), wanted)]
    point = unique_option(engine, wanted, points)
    if point is not None:
        return point
    for scale in scales:
        result, unused = engine.read_saved(frame, 'mode-menu-' + wanted,
                                          region=region, scale=scale)
        points = []
        for line in result.get('details', []):
            if compact(line['text']) != LABELS[wanted] or not line.get('words'):
                continue
            boxes = [word['box'] for word in line['words']]
            if any(len(b) != 4 or b[0] < 0 or b[1] < 0 or b[2] <= 0 or b[3] <= 0
                   or b[0] + b[2] > width or b[1] + b[3] > height for b in boxes):
                continue
            left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
            right = max(b[0] + b[2] for b in boxes)
            bottom = max(b[1] + b[3] for b in boxes)
            point = [round(x + (left + right) / 2), round(y + (top + bottom) / 2)]
            if point not in points:
                points.append(point)
        point = unique_option(engine, wanted, points)
        if point is not None:
            return point
    return None


def select_unknown(engine, wanted):
    # A previous failed lookup may have left the menu open. Read it before
    # clicking the toggle again so confirmation resumes the same selection.
    point = None
    if getattr(engine, '_mode_menu_pending', False):
        point = option(engine, wanted, engine.game.frame())
    if point is None:
        engine.note('当前魔方功能未识别，点击功能下拉按钮寻找“' + LABELS[wanted] + '”。')
        engine._mode_menu_pending = True
        engine.game.click(engine.config['points']['mode_menu'])
        engine.game.wait(0.4)
        point = option(engine, wanted, engine.game.frame())
    if point is None:
        path = engine.save_state('mode-menu-option-unrecognized')
        raise RuntimeError('未能在魔方功能下拉菜单中唯一识别“' + LABELS[wanted]
                           + '”，请展开对应选项后继续。截图：' + path)
    engine.note('已找到功能菜单中的“' + LABELS[wanted] + f'”，点击：{point}。')
    engine.game.click(point)
    engine._mode_menu_pending = False
    engine.neutral()
