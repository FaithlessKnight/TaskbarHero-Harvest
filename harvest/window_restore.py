"""Recover displaced game tooltips through the user's graphics settings flow."""
import copy

from inventory import same_cells
from panels import inventory_positions
from vision import asset, find_matches, name_mask
from windows_io import FocusLost


def unique(points):
    return points[0] if len(points) == 1 else None


def header(frame):
    return unique(find_matches(frame, asset('panel-settings'), mean_limit=4, changed_limit=.02))


def bounded(frame, x, y, width, height):
    left, top = max(0, x), max(0, y)
    right, bottom = min(frame.width, x + width), min(frame.height, y + height)
    return [left, top, max(0, right - left), max(0, bottom - top)]


def settings_button(frame):
    heroes = find_matches(frame, asset('panel-hero'), mean_limit=4, changed_limit=.02)
    if len(heroes) != 1:
        return None
    x, y = heroes[0]
    hits = find_matches(frame, asset('settings-button'), bounded(frame, x + 80, y - 12, 85, 45),
                        mean_limit=4, changed_limit=.02)
    point = unique(hits)
    return [point[0] + 13, point[1] + 13] if point else None


def graphics_button(frame, origin):
    region = bounded(frame, origin[0] - 125, origin[1] + 30, 300, 65)
    for name in ('settings-graphics', 'settings-graphics-selected'):
        ref = asset(name)
        point = unique(find_matches(frame, ref, region, mean_limit=4, changed_limit=.02))
        if point:
            return [point[0] + ref.width // 2, point[1] + ref.height // 2]
    return None


def restore_button(frame, origin):
    region = bounded(frame, origin[0] - 125, origin[1] + 75, 300, 310)
    points = []
    ref = asset('settings-restore-row')
    label = name_mask(asset('settings-restore-label')).template()
    for x, y in find_matches(frame, ref, region):
        # Full-row tolerance also matches the similar Shift+F11 control.
        # The exact label through Shift+F12 must agree before clicking.
        if name_mask(frame.crop([x + 6, y + 6, 150, 21])).matches(label, 1, .005):
            points.append([x + 227, y + 16])
    return unique(points)


def close_button(frame, origin):
    ref = asset('panel-close')
    point = unique(find_matches(frame, ref, bounded(frame, origin[0] + 100, origin[1] - 12, 85, 45),
                                mean_limit=4, changed_limit=.02))
    return [point[0] + ref.width // 2, point[1] + ref.height // 2] if point else None


def anchors(frame):
    result = {'inventory': unique(inventory_positions(frame))}
    for name in ('cube', 'warehouse', 'hero'):
        result[name] = unique(find_matches(frame, asset('panel-' + name), mean_limit=4, changed_limit=.02))
    return result if all(point is not None for point in result.values()) else None


def cells(frame, config, kind):
    from engine import grid, cell_box
    cols, rows = (3, 3) if kind == 'cube' else (config['bag_columns'], config['bag_rows'])
    return [frame.crop(cell_box(config, kind, point)) for point in grid(config, kind, cols, rows)]


def shifted_config(config, before, after):
    result = copy.deepcopy(config)
    deltas = {key: [after[key][n] - before[key][n] for n in (0, 1)] for key in before}
    for key, point in result['points'].items():
        if key == 'neutral':
            result['points'][key] = [after['hero'][0] + 23, after['hero'][1] + 11]
            continue
        panel = 'inventory' if key.startswith('inventory_') else 'warehouse' if key.startswith('warehouse_') else 'cube'
        result['points'][key] = [point[n] + deltas[panel][n] for n in (0, 1)]
    for key, box in result.get('regions', {}).items():
        result['regions'][key] = [box[0] + deltas['cube'][0], box[1] + deltas['cube'][1], *box[2:]]
    return result


def fail(engine, message):
    path = engine.save_state('window-position-restore-unconfirmed')
    raise RuntimeError(message + '；恢复流程保留，确认后继续。截图：' + path)


def wait_for(engine, reader, message):
    for unused in range(8):
        frame = engine.game.frame()
        value = reader(frame)
        if value is not None:
            return value
        engine.game.wait(.25)
    fail(engine, message)


def restore_positions(engine):
    state = engine.__dict__.get('_window_restore')
    if state is None:
        engine.neutral()
        frame = engine.game.frame()
        original = anchors(frame)
        if original is None:
            fail(engine, '恢复前无法唯一定位背包、仓库和魔方')
        mode = getattr(engine, 'current_mode', 'corrode')
        regions = ['mode_label', 'options_' + mode] + (['recipe_synthesize'] if mode == 'synthesize' else [])
        state = {'phase': 'open', 'config': copy.deepcopy(engine.config), 'anchors': original,
                 'inventory': cells(frame, engine.config, 'inventory'),
                 'cube': cells(frame, engine.config, 'cube'),
                 'controls': {key: frame.crop(engine.config['regions'][key])
                              for key in regions if key in engine.config.get('regions', {})}}
        engine._window_restore = state
        engine.note('提示框远离鼠标，打开设置 → 图形 → 恢复窗口位置 → 关闭设置。')
    if state['phase'] == 'open':
        if header(engine.game.frame()) is None:
            point = wait_for(engine, settings_button, '未能唯一识别英雄栏的设置按钮')
            engine.game.click(point)
        wait_for(engine, header, '设置页面打开未确认')
        state['phase'] = 'graphics'
    if state['phase'] == 'graphics':
        origin = wait_for(engine, header, '设置页面未确认')
        frame = engine.game.frame()
        if restore_button(frame, origin) is None:
            point = graphics_button(frame, origin)
            if point is None:
                fail(engine, '未能唯一识别图形设置页签')
            engine.game.click(point)
        wait_for(engine, lambda frame: restore_button(frame, header(frame)) if header(frame) else None,
                 '图形设置中的恢复窗口位置一行未确认')
        state['phase'] = 'restore'
    if state['phase'] == 'restore':
        origin = wait_for(engine, header, '恢复前设置页面未确认')
        point = restore_button(engine.game.frame(), origin)
        if point is None:
            fail(engine, '未能唯一识别恢复窗口位置右侧的恢复按钮')
        state['phase'] = 'close'
        try:
            engine.game.click(point)
        except FocusLost as error:
            if error.input_started is False:
                state['phase'] = 'restore'
            raise
        engine.game.wait(.5)
        engine.note('已点击恢复窗口位置，关闭设置页面。')
    if state['phase'] == 'close':
        frame = engine.game.frame()
        origin = header(frame)
        if origin is not None:
            point = close_button(frame, origin)
            if point is None:
                fail(engine, '未能唯一识别设置页面右上角的关闭按钮')
            engine.game.click(point)
        wait_for(engine, lambda frame: True if header(frame) is None and anchors(frame) else None,
                 '关闭设置后背包页面未确认')
        state['phase'] = 'verify'
    if state['phase'] == 'verify':
        frame = engine.game.frame()
        current = anchors(frame)
        if current is None or header(frame) is not None:
            fail(engine, '恢复后的页面定位未确认')
        config = shifted_config(state['config'], state['anchors'], current)
        # Apply current panel coordinates before a possible pause, so returning
        # already-added inputs can never use coordinates from before reset.
        engine.config = config
        pager = engine.__dict__.get('_inventory_pager')
        if pager is not None:
            from engine import grid, cell_box
            pager.config = config
            pager.points = grid(config, 'inventory', config['bag_columns'], config['bag_rows'])
            pager.boxes = [cell_box(config, 'inventory', p) for p in pager.points]
        for key in ('inventory', 'cube'):
            if not same_cells(state[key], cells(frame, config, key)):
                fail(engine, '恢复后背包或魔方物品变化，尚未继续投料')
        for key, reference in state['controls'].items():
            if not frame.crop(config['regions'][key]).matches(reference.template(), 5, .03):
                fail(engine, '恢复后魔方功能、配方或复选框变化，尚未继续投料')
        engine._stack_cache = {}
        engine.baseline_frame = frame
        engine.__dict__.pop('_window_restore', None)
        engine.note('已关闭设置并确认背包、魔方内容及控件不变，继续识别当前物品。')
