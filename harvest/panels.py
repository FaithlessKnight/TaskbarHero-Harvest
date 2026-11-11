"""Expand, open and collapse game panels with a confirmation after each click."""
from vision import asset, find_matches
from rules import compact
from windows_io import Frame, Game


PANELS = (('cube', '魔方'), ('warehouse', '仓库'), ('inventory', '背包'))


class PanelToggleNotFound(RuntimeError):
    """An unconfirmed control is distinct from a failed input or game error."""
    def __init__(self, action, count, path='', *, candidates=()):
        self.action, self.count = action, count
        label = '展开全部页面' if action == 'expand' else '收起全部页面'
        super().__init__(f'无法唯一识别{label}按钮（找到 {count} 处），请显示按钮后继续。'
                         + (f' 候选坐标：{list(candidates)}。' if candidates else '')
                         + (f' 截图：{path}' if path else ''))


def visible(frame, panel):
    if panel != 'inventory':
        return bool(find_matches(frame, asset('panel-' + panel)))
    return bool(inventory_positions(frame))


def inventory_positions(frame):
    border = asset('panel-inventory')
    positions = []
    for x, y in find_matches(frame, asset('panel-inventory-tab')):
        # The static outer border sits just above and to the left of the bag
        # tab. Its template stops BEFORE the first item column. Never match
        # inventory icons or accept unrelated border/tab matches elsewhere.
        left, top = x - 3, y - 131
        if (left >= 0 and top >= 0 and left + border.width <= frame.width
                and top + border.height <= frame.height
                and frame.crop([left, top, border.width, border.height]).matches(border.template())):
            positions.append([x, y])
    return positions


def toolbar_region(frame, engine):
    config = getattr(engine, 'config', None)
    if not isinstance(config, dict):
        return None
    points = config.get('points', {})
    if not all(key in points for key in ('inventory_first', 'inventory_last')):
        return None
    columns, rows = config.get('bag_columns', 0), config.get('bag_rows', 0)
    if columns < 2 or rows < 2:
        return None
    first, last = points['inventory_first'], points['inventory_last']
    dx, dy = (last[0] - first[0]) / (columns - 1), (last[1] - first[1]) / (rows - 1)
    anchors = inventory_positions(frame)
    if len(anchors) == 1:
        # Opening/closing a page can recenter the hero panel before all
        # three pages are open. Locate its toolbar below the current bag
        # tab and grid, rather than restricting it to saved bag coordinates.
        x, y = anchors[0]
        left = x - asset('panel-inventory').width - 3
        top = y + asset('panel-inventory-tab').height
        right = min(frame.width, left + round((columns + 1) * dx))
        bottom = min(frame.height, top + round(dy * 2))
        left, top = max(0, left), max(0, top)
        if right > left and bottom > top:
            return [left, top, right - left, bottom - top]
    left, top = max(0, round(first[0] - dx)), max(0, round(last[1] + dy * 0.85))
    right, bottom = min(frame.width, round(last[0] + dx)), min(frame.height, round(last[1] + dy * 3.6))
    return [left, top, right - left, bottom - top] if right > left and bottom > top else None


def button(frame, panel, engine=None, *, required=True):
    reference = asset('open-' + panel)
    region = toolbar_region(frame, engine) if panel in ('cube', 'warehouse') else None
    # Toolbar labels appear ABOVE hovered icons and overwrite the template's
    # top background pixels. Match the button body, preserving its center.
    top_margin = 12 if panel in ('cube', 'warehouse') else 0
    body = reference.crop([0, top_margin, reference.width, reference.height - top_margin])
    positions = [[x, y - top_margin] for x, y in find_matches(frame, body, region)]
    if not positions and panel in ('cube', 'warehouse'):
        # User-supplied button variants have different outer backgrounds and
        # warehouse brightness. Match their stable icon interior directly.
        alternate = asset('open-' + panel + '-alternate')
        centers = [[x + alternate.width // 2, y + alternate.height // 2]
                   for x, y in find_matches(frame, alternate, region)]
        if len(centers) == 1:
            return centers[0]
        if centers:
            return button_error(engine, panel, centers)
    if not positions and panel == 'cube':
        # Match the cube symbol itself, excluding its surrounding octagonal
        # button. Gold geometry survives normal, dim and hovered palettes.
        symbol = toggle_mask(asset('open-cube-symbol'))
        centers = [[x + symbol.width // 2, y + symbol.height // 2]
                   for x, y in find_matches(toggle_mask(frame), symbol, region,
                                            mean_limit=1, changed_limit=0.005)]
        if len(centers) == 1:
            return centers[0]
        if centers:
            return button_error(engine, panel, centers)
    if not positions and panel == 'inventory' and engine is not None:
        # An inactive bag tab can have a different background. Locate its
        # literal label within the calibrated tab strip instead of guessing
        # the old center or matching only the icon for inventory sorting.
        config = engine.config
        first, last = config['points']['inventory_first'], config['points']['inventory_last']
        step_x = (last[0] - first[0]) / (config['bag_columns'] - 1)
        step_y = (last[1] - first[1]) / (config['bag_rows'] - 1)
        x, y = max(0, int(first[0] - step_x * 0.75)), max(0, int(last[1] + step_y * 0.45))
        region = [x, y, min(int(step_x * 2.6), frame.width - x), min(int(step_y * 1.2), frame.height - y)]
        for scale in (3, 4):
            result, unused = engine.read_saved(frame, 'open-inventory-label', region=region, scale=scale)
            labels = [line['words'] for line in result.get('details', [])
                      if compact(line['text']) == '背包' and line.get('words')]
            if len(labels) == 1:
                boxes = [word['box'] for word in labels[0]]
                left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
                right, bottom = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
                return [round(x + (left + right) / 2), round(y + (top + bottom) / 2)]
    if len(positions) != 1:
        if not positions and not required:
            return None
        return button_error(engine, panel, positions)
    x, y = positions[0]
    return [x + reference.width // 2, y + reference.height // 2]


def button_error(engine, panel, candidates):
    message = '无法唯一识别' + dict(PANELS)[panel] + f'页面按钮（找到 {len(candidates)} 处），请显示按钮后继续。'
    if engine is not None:
        message += f' 截图：{engine.save_state("panel-" + panel + "-button-unrecognized")}'
    raise RuntimeError(message)


def wait_button(engine, panel):
    # Expansion can reveal the panel before its toolbar has finished drawing.
    for unused in range(3):
        point = button(engine.game.frame(), panel, engine, required=False)
        if point is not None:
            return point
        engine.game.wait(0.25)
    return button(engine.game.frame(), panel, engine)


def toggle_mask(frame):
    # Both arrow directions use the same gold palette. Keep the complete
    # silhouette, including dark pixels, so hover brightness cannot change
    # the state and the up/down arrows still require distinct exact shapes.
    pixels = bytearray(frame.width * frame.height * 4)
    for n in range(0, len(pixels), 4):
        b, g, r = frame.pixels[n:n + 3]
        gold = r >= 110 and g >= 70 and g * 100 >= r * 55 and r - b >= 60 and g - b >= 35
        pixels[n:n + 4] = bytes([255 if gold else 0] * 3 + [255])
    return Frame(frame.width, frame.height, bytes(pixels))


def toggle_button(frame, action, *, required=True, engine=None, near=None):
    region = None
    if near is not None:
        # The toggle changes direction at the same location. Other game
        # instances may become visible through the newly collapsed window.
        region = [max(0, near[0] - 16), max(0, near[1] - 16), 32, 32]
    names = [action + '-panels']
    if action == 'collapse':
        names.append('collapse-panels-hover')
    points = []
    for name in names:
        reference = asset(name)
        for x, y in find_matches(frame, reference, region):
            point = [x + reference.width // 2, y + reference.height // 2]
            if not any(max(abs(a - b) for a, b in zip(point, other)) <= 3 for other in points):
                points.append(point)
    if not points:
        # The pointer stays over this button after a toggle. Its newly shown
        # opposite arrow can therefore already be highlighted on the next
        # frame. Match gold geometry as a fallback, never absence alone.
        mask = toggle_mask(frame)
        for name in names:
            reference = toggle_mask(asset(name))
            for x, y in find_matches(mask, reference, region, mean_limit=1, changed_limit=0.005):
                point = [x + reference.width // 2, y + reference.height // 2]
                if not any(max(abs(a - b) for a, b in zip(point, other)) <= 3 for other in points):
                    points.append(point)
    if not points:
        # Match the complete directional arrow with a dark margin, excluding
        # the surrounding button border/background that changes on hover.
        # The opposite direction must still have a different exact silhouette.
        cores = (('collapse-panels', [2, 2, 14, 14]),
                 ('collapse-panels-hover', [2, 4, 14, 14])) if action == 'collapse' else (
                     ('expand-panels', [3, 3, 14, 14]),)
        for name, box in cores:
            reference = asset(name)
            core = toggle_mask(reference.crop(box))
            for x, y in find_matches(mask, core, region, mean_limit=1, changed_limit=.005):
                point = [x - box[0] + reference.width // 2, y - box[1] + reference.height // 2]
                if not any(max(abs(a - b) for a, b in zip(point, other)) <= 3 for other in points):
                    points.append(point)
    if near is not None:
        points = [p for p in points if max(abs(a - b) for a, b in zip(p, near)) <= 4]
    candidates = list(points)
    if len(points) > 1 and engine is not None:
        game = engine.game
        hint = engine.__dict__.get('_panel_toggle_hint') if near is None else near
        preferred = [p for p in points if isinstance(hint, (list, tuple)) and len(hint) == 2
                     and max(abs(a - b) for a, b in zip(p, hint)) <= 4]
        if len(preferred) == 1:
            point = preferred[0]
            if not isinstance(game, Game) or game.can_target(point):
                return point
            points.remove(point)
        if isinstance(game, Game):
            points = [point for point in points if game.can_target(point)]
        engine.note(f'{"展开" if action == "expand" else "收起"}按钮匹配候选：{candidates}；'
                    f'当前窗口可操作候选：{points}；上一确认位置：{hint}。')
    if len(points) == 1:
        return points[0]
    if required or candidates:
        path = engine.save_state('panels-' + action + '-button-unrecognized') if engine is not None else ''
        raise PanelToggleNotFound(action, len(points), path, candidates=candidates)
    return None


def any_visible(frame):
    return any(visible(frame, panel) for panel, label in PANELS)


def remember_toggle(engine, point):
    engine._panel_toggle_hint = list(point)
    engine._panel_toggle_confirmed = list(point)


def expand_if_collapsed(engine):
    frame = engine.game.frame()
    pending = engine.__dict__.get('_panel_expansion')
    if isinstance(pending, dict):
        point = pending['point']
        if any_visible(frame) or toggle_button(frame, 'collapse', required=False, near=point) is not None:
            remember_toggle(engine, point)
            engine.__dict__.pop('_panel_expansion', None)
            return
        if toggle_button(frame, 'expand', required=False, near=point) is None:
            return confirm_expansion(engine, point)
    else:
        if any_visible(frame):
            return
        point = toggle_button(frame, 'expand', required=False, engine=engine)
        if point is None:
            return  # Individual toolbar buttons may already be visible.
        engine._panel_expansion = {'point': point}
    engine.note(f'未检测到页面，点击展开全部页面按钮：{point}。')
    engine.game.click(point)
    confirm_expansion(engine, point)


def confirm_expansion(engine, point):
    for unused in range(8):
        frame = engine.game.frame()
        if any_visible(frame) or toggle_button(frame, 'collapse', required=False, near=point, engine=engine) is not None:
            remember_toggle(engine, point)
            engine.__dict__.pop('_panel_expansion', None)
            return
        engine.game.wait(0.25)
    path = engine.save_state('panels-expand-unconfirmed')
    raise RuntimeError('全部页面展开未确认，请处理后继续。截图：' + path)


def collapse_panels(engine):
    frame = engine.game.frame()
    pending = engine.__dict__.get('_panel_cleanup')
    if isinstance(pending, dict):
        point = pending['point']
        # A resume must not click a button that has already become Expand.
        # Retry input only when the original Collapse direction is confirmed.
        if toggle_button(frame, 'collapse', near=point, required=False) is not None:
            engine.game.click(point)
        return confirm_collapse(engine, pending)
    if already_collapsed(frame):
        engine.note('全部页面已收起。')
        return
    if any_visible(frame):
        engine.neutral()  # Dismiss the last scanned item's tooltip before locating.
        frame = engine.game.frame()
    for attempt in range(3):
        point = toggle_button(frame, 'collapse', required=False, engine=engine)
        if point is not None:
            break
        engine.game.wait(.25)
        frame = engine.game.frame()
        if already_collapsed(frame):
            engine.note('全部页面已收起。')
            return
    else:
        point = toggle_button(frame, 'collapse', engine=engine)
    markers = []
    for panel, label in PANELS:
        name = 'panel-inventory-tab' if panel == 'inventory' else 'panel-' + panel
        reference = asset(name)
        positions = inventory_positions(frame) if panel == 'inventory' else find_matches(frame, reference)
        markers.extend((name, position) for position in positions)
    pending = {'point': point, 'markers': markers}
    engine._panel_cleanup = pending
    engine.note(f'本进程收菜完成，点击收起全部页面按钮：{point}。')
    engine.game.click(point)
    confirm_collapse(engine, pending)


def already_collapsed(frame):
    if any_visible(frame):
        return False
    try:
        return toggle_button(frame, 'expand', required=False) is not None
    except PanelToggleNotFound:
        # No click has been issued. Keep looking for a unique Collapse
        # control; its bounded failure can leave cleanup to the user.
        return False


def confirm_collapse(engine, pending):
    # Do not move to the calibrated neutral point: its panel is now hidden.
    stable = 0
    for unused in range(8):
        frame = engine.game.frame()
        still_open = False
        for name, (x, y) in pending['markers']:
            reference = asset(name)
            region = [max(0, x - 4), max(0, y - 4), reference.width + 8, reference.height + 8]
            if find_matches(frame, reference, region):
                still_open = True
                break
        closed = toggle_button(frame, 'expand', required=False, near=pending['point']) is not None and not still_open
        stable = stable + 1 if closed else 0
        if stable >= 2:
            engine.__dict__.pop('_panel_cleanup', None)
            remember_toggle(engine, pending['point'])
            engine.note('已确认全部页面收起，可以切换下一个进程。')
            return
        engine.game.wait(0.25)
    path = engine.save_state('panels-collapse-unconfirmed')
    raise RuntimeError('全部页面收起未确认，请处理后继续。截图：' + path)


def ensure_panels(engine):
    expand_if_collapsed(engine)
    frame = engine.game.frame()
    if all(visible(frame, panel) for panel, label in PANELS):
        engine.note('已确认魔方、仓库和背包页面均已打开。')
        return
    # Use the collapse control to dismiss tooltips when available. The cube
    # button is not a prerequisite for recognizing an already-open cube.
    anchor = toggle_button(frame, 'collapse', required=False, engine=engine)
    if anchor is not None:
        engine.game.move(anchor)
        engine.game.wait(0.4)
    for panel, label in PANELS:
        frame = engine.game.frame()
        if visible(frame, panel):
            continue
        point = wait_button(engine, panel)
        engine.note(f'未检测到{label}页面，点击打开按钮：{point}。')
        engine.game.click(point)
        engine.game.move(anchor if anchor is not None else point)
        engine.game.wait(0.4)
        for unused in range(8):
            if visible(engine.game.frame(), panel):
                break
            engine.game.wait(0.25)
        else:
            path = engine.save_state('panel-' + panel + '-unconfirmed')
            raise RuntimeError(label + '页面打开未确认，请处理后继续。截图：' + path)
    engine.note('已确认魔方、仓库和背包页面均已打开。')
