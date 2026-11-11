"""Portable JSON preferences, with independently selectable calibration and rules."""
import base64
import copy
import json
import math
from pathlib import Path

from rules import corrosion_selection
from supplemental import normalize_rules


FORMAT = 'TaskbarHero-Harvest-Config'
MAX_BYTES = 8 * 1024 * 1024
SETTINGS = {'hover_seconds': 1.2, 'action_interval_seconds': .8,
            'max_batches': 20, 'auto_interval_minutes': 30, 'corrosion_selection': None}
GRIDS = {'bag_columns': (7, 2, 12), 'bag_rows': (3, 2, 3), 'bag_total_rows': (15, 3, 15),
         'warehouse_columns': (7, 2, 12), 'warehouse_rows': (7, 2, 15)}
CALIBRATION_KEYS = set(GRIDS) | {'window_size', 'points', 'regions', 'learned'}


def number(value, low, high, *, integer=False):
    if (type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value)
            or (integer and type(value) is not int)):
        raise ValueError(f'配置数值需为 {low}～{high}' + (' 的整数。' if integer else '。'))
    return value


def settings(value):
    if not isinstance(value, dict) or set(value) != set(SETTINGS):
        raise ValueError('常规设置字段缺失或无效。')
    return {'hover_seconds': number(value['hover_seconds'], .3, 5),
            'action_interval_seconds': number(value['action_interval_seconds'], .5, 10),
            'max_batches': number(value['max_batches'], 1, 100, integer=True),
            'auto_interval_minutes': number(value['auto_interval_minutes'], 1, 1440),
            'corrosion_selection': corrosion_selection(value['corrosion_selection'])}


def coordinates(value, length, limits):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError('窗口校准坐标格式无效。')
    return [number(n, 0, limit, integer=True) for n, limit in zip(value, limits)]


def template(value):
    if not isinstance(value, dict) or set(value) != {'width', 'height', 'pixels'}:
        raise ValueError('界面记录图像字段无效。')
    w, h = [number(value[k], 1, 4096, integer=True) for k in ('width', 'height')]
    encoded = value['pixels']
    if w * h > 1024 * 1024 or not isinstance(encoded, str) or len(encoded) > MAX_BYTES:
        raise ValueError('界面记录图像过大或无效。')
    try:
        pixels = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError('界面记录图像编码无效。') from error
    if len(pixels) != w * h * 4:
        raise ValueError('界面记录图像尺寸不符。')
    return dict(value)


def calibration(value):
    from engine import POINT_LABELS, REGION_LABELS, cell_box, grid
    if not isinstance(value, dict) or set(value) != CALIBRATION_KEYS:
        raise ValueError('窗口校准字段缺失或无效。')
    result = {key: number(value[key], lo, hi, integer=True) for key, (_, lo, hi) in GRIDS.items()}
    if result['bag_rows'] > result['bag_total_rows']:
        raise ValueError('背包总行数不能小于可见行数。')
    size = value['window_size']
    if size is not None:
        size = coordinates(size, 2, [32768, 32768])
        if min(size) < 1:
            raise ValueError('校准窗口尺寸无效。')
    result['window_size'] = size
    width, height = size or [32768, 32768]
    points, regions = value['points'], value['regions']
    if not isinstance(points, dict) or set(points) - set(POINT_LABELS):
        raise ValueError('校准点位无效。')
    if not isinstance(regions, dict) or set(regions) - set(REGION_LABELS):
        raise ValueError('校准区域无效。')
    result['points'] = {k: coordinates(v, 2, [width - 1, height - 1]) for k, v in points.items()}
    result['regions'] = {}
    for key, box in regions.items():
        x, y, w, h = coordinates(box, 4, [width - 1, height - 1, width, height])
        if min(w, h) < 1 or x + w > width or y + h > height:
            raise ValueError('校准区域超出窗口或没有面积。')
        result['regions'][key] = [x, y, w, h]
    for key, cols, rows in (('inventory', result['bag_columns'], result['bag_rows']),
                            ('warehouse', result['warehouse_columns'], result['warehouse_rows']), ('cube', 3, 3)):
        if key + '_first' in points and key + '_last' in points:
            try:
                grid(result, key, cols, rows)
            except RuntimeError as error:
                raise ValueError(str(error)) from error
    learned = value['learned']
    if not isinstance(learned, dict) or set(learned) - {'corrode', 'synthesize'}:
        raise ValueError('界面记录模式无效。')
    result['learned'] = {}
    for mode, record in learned.items():
        keys = {'empty_board', 'mode_label', 'options_' + mode}
        if mode == 'synthesize':
            keys |= {'recipe_synthesize', 'empty_warehouse'}
        if not isinstance(record, dict) or set(record) != keys:
            raise ValueError('界面记录字段缺失或无效。')
        board = record['empty_board']
        if not isinstance(board, list) or len(board) != 9:
            raise ValueError('魔方空格记录必须是九格。')
        normalized = {'empty_board': [template(t) for t in board]}
        normalized.update({k: template(record[k]) for k in keys - {'empty_board'}})
        try:
            for key in keys - {'empty_board', 'empty_warehouse'}:
                if [normalized[key]['width'], normalized[key]['height']] != result['regions'][key][2:]:
                    raise ValueError('界面记录与校准区域尺寸不符。')
            for t, point in zip(normalized['empty_board'], grid(result, 'cube', 3, 3)):
                if [t['width'], t['height']] != cell_box(result, 'cube', point)[2:]:
                    raise ValueError('魔方空格图像与校准尺寸不符。')
            if mode == 'synthesize':
                t = normalized['empty_warehouse']
                if [t['width'], t['height']] != cell_box(result, 'warehouse', points['warehouse_empty'])[2:]:
                    raise ValueError('仓库空格图像与校准尺寸不符。')
        except KeyError as error:
            raise ValueError('界面记录缺少对应校准点位或区域。') from error
        if size is None:
            raise ValueError('界面记录缺少校准窗口尺寸。')
        result['learned'][mode] = normalized
    return result


def export_data(config, *, include_calibration=False, include_items=False):
    data = {'format': FORMAT, 'version': 1,
            'settings': settings({k: config.get(k, v) for k, v in SETTINGS.items()})}
    if include_calibration:
        values = {k: copy.deepcopy(config.get(k, default)) for k, default in
                  [('window_size', None), ('points', {}), ('regions', {}), ('learned', {})]}
        values.update({k: config.get(k, default) for k, (default, _, _) in GRIDS.items()})
        data['calibration'] = calibration(values)
    if include_items:
        data['supplemental_items'] = normalize_rules(config.get('supplemental_items'))
    return data


def validate_data(data):
    if (not isinstance(data, dict) or data.get('format') != FORMAT
            or type(data.get('version')) is not int or data['version'] != 1
            or set(data) - {'format', 'version', 'settings', 'calibration', 'supplemental_items'}):
        raise ValueError('不是受支持的收菜助手配置文件。')
    result = {'format': FORMAT, 'version': 1, 'settings': settings(data.get('settings'))}
    if 'calibration' in data:
        result['calibration'] = calibration(data['calibration'])
    if 'supplemental_items' in data:
        result['supplemental_items'] = normalize_rules(data['supplemental_items'])
    return result


def merge_config(current, data, *, include_calibration=False, include_items=False):
    data = validate_data(data)
    result = copy.deepcopy(current)
    result.update(data['settings'])
    if include_calibration and 'calibration' in data:
        result.update(data['calibration'])
    if include_items and 'supplemental_items' in data:
        result['supplemental_items'] = data['supplemental_items']
    return result


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('配置文件有重复字段：' + key)
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('配置文件含非有限数值：' + value)


def load_file(path):
    with Path(path).open('rb') as source:
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('配置文件超过 8 MB。')
    try:
        return validate_data(json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_object,
                                        parse_constant=reject_constant))
    except (UnicodeError, RecursionError) as error:
        raise ValueError('配置文件编码或结构无效。') from error


def save_file(path, data):
    import os
    import tempfile
    payload = json.dumps(validate_data(data), ensure_ascii=False, indent=2).encode('utf-8')
    if len(payload) > MAX_BYTES:
        raise ValueError('导出配置超过 8 MB。')
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as out:
            temporary = Path(out.name)
            out.write(payload)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
