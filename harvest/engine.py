from datetime import datetime
from dataclasses import replace
import hashlib
from itertools import zip_longest
import json
import re
from pathlib import Path
import time

from rules import (Item, batches, classify, compact, has_item_details,
                   parse_stack_repetition, corrosion_selection, selected_for_corrosion,
                   SELECTABLE_QUALITIES, REQUIRED_LEVEL_LABEL, WAREHOUSE_QUALITIES,
                   QUALITY_NAMES, OTHER_QUALITIES, SYNTHESIS_QUALITIES, INSCRIPTION_STORAGE_QUALITIES)
from windows_io import Frame, Game, OCR, ROOT, FocusLost
from inventory import InventoryPager, sort_button, same_cells, same_warehouse_cells
from vision import header_qualities, header_names, header_material_types, header_equipment_stats, find_matches
from warehouse_pages import StorageResult, WarehousePager


POINT_LABELS = {
    'inventory_first': '背包可见区域第一格中心', 'inventory_last': '背包可见区域最后一格中心（第3行末格）',
    'neutral': '移开提示的空白位置', 'mode_menu': '魔方功能下拉按钮',
    'mode_corrode': '菜单中的腐蚀选项', 'mode_synthesize': '菜单中的合成选项',
    'cube_first': '魔方九宫格左上格中心', 'cube_last': '魔方九宫格右下格中心',
    'commit_corrode': '腐蚀执行按钮', 'commit_synthesize': '合成执行按钮',
    'warehouse_first': '当前仓库页第一格中心', 'warehouse_last': '当前仓库页最后一格中心',
    'warehouse_empty': '当前仓库页一个空格中心',
    'inventory_sort': '背包整理按钮（可选；默认自动识别）',
}
REGION_LABELS = {
    'mode_label': '魔方功能名称区域（仅腐蚀/合成文字）',
    'options_corrode': '腐蚀：包含仓库物品复选框区域',
    'options_synthesize': '合成：包含仓库物品和固定等级区域',
    'recipe_synthesize': '合成：等级配方区域（例如 Lv.80~90）',
}


def config_hash(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def grid(config, key, columns, rows):
    first, last = config['points'][key + '_first'], config['points'][key + '_last']
    if columns < 2 or rows < 2 or last[0] <= first[0] or last[1] <= first[1]:
        raise RuntimeError('网格坐标或行列数不正确。')
    return [[round(first[0] + col * (last[0] - first[0]) / (columns - 1)),
             round(first[1] + row * (last[1] - first[1]) / (rows - 1))]
            for row in range(rows) for col in range(columns)]


def cell_box(config, key, point):
    if key == 'cube':
        columns, rows = 3, 3
    else:
        prefix = 'bag' if key == 'inventory' else 'warehouse'
        columns, rows = config[prefix + '_columns'], config[prefix + '_rows']
    first, last = config['points'][key + '_first'], config['points'][key + '_last']
    radius_x = max(3, int((last[0] - first[0]) / (columns - 1) / 2) - 4)
    radius_y = max(3, int((last[1] - first[1]) / (rows - 1) / 2) - 4)
    return [point[0] - radius_x, point[1] - radius_y, radius_x * 2, radius_y * 2]


def validate(config, execution=False):
    from supplemental import normalize_rules
    normalize_rules(config.get('supplemental_items'))
    corrosion_selection(config.get('corrosion_selection'))
    point_keys = [key for key in POINT_LABELS if key != 'inventory_sort'] if execution else ('inventory_first', 'inventory_last', 'neutral')
    missing = [POINT_LABELS[k] for k in point_keys if k not in config.get('points', {})]
    if execution:
        missing += [label for k, label in REGION_LABELS.items() if k not in config.get('regions', {})]
        missing += [f'学习{label}界面' for mode, label in (('corrode', '腐蚀'), ('synthesize', '合成'))
                    if mode not in config.get('learned', {})]
    if missing:
        raise RuntimeError('尚未校准：' + '、'.join(missing))
    if not config.get('window_size'):
        raise RuntimeError('请先截取游戏窗口并校准。')
    for key, cols, rows in (('inventory', config['bag_columns'], config['bag_rows']),
                            ('cube', 3, 3), ('warehouse', config['warehouse_columns'], config['warehouse_rows'])):
        if key + '_first' in config['points'] and key + '_last' in config['points']:
            grid(config, key, cols, rows)
    if not config['bag_rows'] <= config.get('bag_total_rows', 15) <= 15:
        raise RuntimeError('背包总行数应不小于可见行数，且最多为 15 行。')


class Engine:
    def __init__(self, config, stop, log, progress=lambda items: None, *, target=None):
        self.config, self.stop, self.log, self.progress = config, stop, log, progress
        self.game = Game(stop) if target is None else Game(stop, target=target)
        if list(self.game.original_size) != config['window_size']:
            raise RuntimeError(f'窗口尺寸 {list(self.game.original_size)} 与校准尺寸 {config["window_size"]} 不同。'
                               '请把该窗口恢复到校准尺寸后继续，或停止任务后重新校准。')
        try:
            self.game.activate()
        except FocusLost:
            self.game.recover_focus()
        self.ocr = OCR()
        try:
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            if target is not None:
                stamp += f'-pid{target.pid}'
            self.folder = ROOT / 'runs' / stamp
            self.folder.mkdir(parents=True)
            self.serial = 0
            self.session_log = self.folder / 'events.txt'
            self.game._focus_log = self.note
        except Exception:
            self.ocr.close()
            raise

    def close(self):
        try:
            if isinstance(getattr(self.game, 'last_move', None), dict):
                (self.folder / 'pointer.json').write_text(
                    json.dumps(self.game.last_move, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            self.ocr.close()

    def note(self, text):
        self.log(text)
        with self.session_log.open('a', encoding='utf-8') as stream:
            stream.write(datetime.now().isoformat(timespec='seconds') + ' ' + text + '\n')

    def read_frame(self, name, region=None, scale=2, language='zh'):
        self.serial += 1
        width, height = self.game.original_size if region is None else region[2:]
        scale = min(scale, max(1, self.ocr.max_size // max(width, height)))
        if max(width, height) * scale > self.ocr.max_size:
            raise RuntimeError('窗口超过 Windows OCR 尺寸上限，请缩小游戏窗口后重新校准。')
        path = self.folder / f'{self.serial:05d}-{name}.bmp'
        self.game.frame(scale, region).save(path)
        return self.ocr.read(path, language=language), str(path)

    def read_saved(self, frame, name, region=None, scale=3, language='zh', *, interpolation='nearest'):
        self.serial += 1
        if region is not None:
            frame = frame.crop(region)
        path = self.folder / f'{self.serial:05d}-{name}.bmp'
        frame.save(path)
        scale = min(scale, max(1, self.ocr.max_size // max(frame.width, frame.height)))
        options = {'interpolation': interpolation} if interpolation != 'nearest' else {}
        return self.ocr.read(path, scale=scale, language=language, **options), str(path)

    def save_state(self, name):
        self.serial += 1
        path = self.folder / f'{self.serial:05d}-{name}.bmp'
        self.game.frame().save(path)
        return str(path)

    def read_stack_count(self, frame, key, point):
        if not isinstance(frame, Frame):
            return None
        patch = frame.crop(cell_box(self.config, key, point))
        cache = getattr(self, '_stack_cache', {})
        self._stack_cache = cache
        token = (patch.width, patch.height, patch.pixels)
        if token in cache:
            return cache[token]
        glyphs, blank = patch.stack_glyphs()
        count = 1 if blank and not glyphs else None
        if len(glyphs) == 1:
            values = []
            for copies in (5, 8):
                result, unused = self.read_saved(glyphs[0].repeated(copies).binary(),
                    f'{key}-stack-{copies}', scale=5, language='en')
                values.append(parse_stack_repetition(result['text'], copies))
            if values[0] is not None and values[0] == values[1]:
                count = values[0]
            elif len({v for v in values if v is not None}) == 1:
                # A long repeated run can be ignored entirely by Windows OCR.
                # Try another length; still require two identical valid reads
                # and never resolve two conflicting numerals by a vote.
                known = next(v for v in values if v is not None)
                for copies in (6, 7):
                    result, unused = self.read_saved(glyphs[0].repeated(copies).binary(),
                        f'{key}-stack-{copies}', scale=5, language='en')
                    extra = parse_stack_repetition(result['text'], copies)
                    if extra is not None:
                        if extra == known:
                            count = known
                        break
        cache[token] = count
        return count

    def neutral(self):
        self.game.move(self.config['points']['neutral'])
        self.game.wait(0.4)

    def pager(self):
        if not hasattr(self, '_inventory_pager'):
            points = grid(self.config, 'inventory', self.config['bag_columns'], self.config['bag_rows'])
            self._inventory_pager = InventoryPager(self.game, self.config, points,
                [cell_box(self.config, 'inventory', p) for p in points], self.neutral, self.note)
        return self._inventory_pager

    def inventory_point(self, slot):
        return self.pager().point(slot)

    def show_inventory_item(self, item):
        self.pager().seek(item.view_top)
        return self.inventory_point(item.slot)

    def baseline(self):
        self.neutral()
        result, unused = self.read_frame('baseline')
        text = compact(result['text'])
        if any(q + '级' in text for q in QUALITY_NAMES + OTHER_QUALITIES) or '供奉' in text:
            raise RuntimeError('空白位置没有关闭物品提示，请重新校准空白位置。')
        self.baseline_frame = self.game.frame()
        return result['lines']

    def local_item(self, slot, key, point):
        before = getattr(self, 'baseline_frame', None)
        if not isinstance(before, Frame):
            return None
        after = self.game.frame()
        cols = 3 if key == 'cube' else self.config['bag_columns']
        step = (self.config['points'][key + '_last'][0] - self.config['points'][key + '_first'][0]) / (cols - 1)
        selection_diagnostics = None
        if isinstance(after, Frame) and isinstance(getattr(after, 'pixels', None), bytes):
            from tooltip_regions import item_regions
            selection_diagnostics = {}
            regions = item_regions(after, before, point, step, cell_box(self.config, key, point),
                                   diagnostics=selection_diagnostics)
            self._last_tooltip_selection = selection_diagnostics
        else:
            regions = after.tooltip_boxes(before, point, step)
        if not regions:
            if selection_diagnostics is not None:
                self.serial += 1
                path = self.folder / f'{self.serial:05d}-{key}-{slot:02d}-tooltip-unmatched.bmp'
                after.save(path)
                path.with_suffix('.json').write_text(json.dumps(selection_diagnostics,
                    ensure_ascii=False, indent=2), encoding='utf-8')
            return None
        # tooltip_boxes orders independent panels by distance to the pointer.
        # Never combine the hovered item with an equipped comparison panel.
        region = regions[0]
        # Synthetic margin helps OCR at the edge without including neighbours.
        previous_frame, current_frame = before.crop(region).padded(), after.crop(region).padded()
        previous, unused = self.read_saved(previous_frame, f'{key}-{slot:02d}-local-before')
        current, path = self.read_saved(current_frame, f'{key}-{slot:02d}-local')
        second, unused = self.read_saved(current_frame, f'{key}-{slot:02d}-local-scale2', scale=2)
        second_before, unused = self.read_saved(previous_frame, f'{key}-{slot:02d}-local-before-scale2', scale=2)
        title_sources = (current.copy(), second.copy())
        previous['lines'] = previous['lines'] + second_before['lines']
        # Keep both versions of the header near the start: the classifier
        # accepts suffix-less rarity only in the first six header lines.
        current['lines'] = [line for pair in zip_longest(current['lines'], second['lines'])
                            for line in pair if line is not None]
        current['text'] += '\n' + second['text']
        Path(path).with_suffix('.json').write_text(json.dumps(
            {'point': point, 'selected': region, 'candidates': regions, 'text': current['text'],
             'frame_pixels': [after.width, after.height] if isinstance(getattr(after, 'width', None), int) else None,
             'tooltip_pixels': [current_frame.width, current_frame.height],
             'tooltip_selection': selection_diagnostics},
            ensure_ascii=False, indent=2), encoding='utf-8')
        if any(marker in compact(current['text']).upper() for marker in ('EQUIPPED', '已装备', '已裝備')):
            return Item(slot, 'keep', '', '', None, '检测到已装备对比框，未确认当前物品，保留', current['text'], path)
        selection = corrosion_selection(self.config.get('corrosion_selection'))
        # Check the actual rarity glyphs even if whole-tooltip OCR guessed a
        # lower quality. Conflicting evidence must never authorize corrosion.
        header = header_qualities(current_frame)
        names = header_names(current_frame)
        types = header_material_types(current_frame) + header_equipment_stats(current_frame)
        item = classify(slot, names + current['lines'] + types, previous['lines'],
                        full_text=current['text'], selection=selection, quality_evidence=header)
        if (not item.quality and item.reason == '品质缺失或冲突，保留'
                and not header):
            extra = self.rarity_lines(current_frame, f'{key}-{slot:02d}')
            item = classify(slot, names + current['lines'] + types, previous['lines'],
                            full_text=current['text'] + '\n' + '\n'.join(extra), selection=selection,
                            quality_evidence=extra)
        enhanced = None
        needs_header = (item.reason in ('品质缺失或冲突，保留', '物品类型未确认，保留',
                                        '供奉材料类型未确认，保留')
                        or (item.kind == 'material' and not item.material_type and item.action != 'store'))
        if needs_header and isinstance(getattr(after, 'pixels', None), bytes):
            enhanced = self.enhance_header(current_frame, f'{key}-{slot:02d}')
            if enhanced is not None:
                # Only repeated exact fields authorize an action. A conflicting
                # quality from either read blocks a previously lower quality.
                observed = enhanced['observed_qualities']
                qualities = observed if (len(observed) > 1 or
                    (item.quality and any(q != item.quality for q in observed))) else enhanced['qualities']
                prior = [item.quality] if item.quality else []
                combined_types = types + enhanced['labels']
                item = classify(slot, names + current['lines'] + combined_types, previous['lines'],
                                full_text=current['text'] + '\n' + enhanced['text'], selection=selection,
                                quality_evidence=header + prior + qualities)
                from tooltip_ocr import fields
                original_labels = fields({'lines': current['lines']})[1]
                material_labels = {label for label in set(combined_types) | original_labels if label.endswith('材料')}
                if enhanced['blocked'] or len(material_labels) > 1 or len(set(header + prior + observed)) > 1:
                    item = replace(item, action='keep', reason='头部补读出现锁定、对比或字段冲突，保留')
                Path(path).with_suffix('.json').write_text(json.dumps(
                    {'point': point, 'selected': region, 'candidates': regions, 'text': current['text'],
                     'frame_pixels': [after.width, after.height],
                     'tooltip_pixels': [current_frame.width, current_frame.height],
                     'tooltip_selection': selection_diagnostics,
                     'header_enhancement': enhanced}, ensure_ascii=False, indent=2), encoding='utf-8')
        details = names + types + current['lines'] + (enhanced['labels'] if enhanced else [])
        if self.config.get('supplemental_items'):
            from supplemental import (title_from_details, title_from_crop, apply_rules,
                                      title_view, resolved_readings)
            readings = [title_from_details(result) for result in title_sources]
            title_attempts = [readings]
            registered = {row['name'] for row in self.config['supplemental_items']}
            # Supplemental names come only from a restored title frame. Never
            # treat the first body/type line of an unframed crop as its name.
            framed = selection_diagnostics and any(
                candidate.get('framed_box') == region for candidate in selection_diagnostics.get('candidates', []))
            if framed:
                if not readings[0] or readings[0] != readings[1] or readings[0] not in registered:
                    title = current_frame.crop([4, 4, current_frame.width - 8, min(30, current_frame.height - 8)]).padded(8)
                    readings = [title_from_crop(self.read_saved(title, f'{key}-{slot:02d}-custom-title',
                        scale=scale, interpolation='cubic')[0]) for scale in (4, 5)]
                    title_attempts.append(readings)
                    if not readings[0] or readings[0] != readings[1] or readings[0] not in registered:
                        view = title_view(current_frame)
                        if view is not None:
                            from vision import name_mask
                            for label, isolated in (('mask', name_mask(view)), ('tight', view)):
                                pair = [title_from_crop(self.read_saved(isolated.padded(12),
                                    f'{key}-{slot:02d}-custom-title-{label}', scale=scale,
                                    interpolation='cubic')[0]) for scale in (4, 5)]
                                title_attempts.append(pair)
                                if pair[0] and pair[0] == pair[1] and pair[0] in registered:
                                    break
                readings = resolved_readings(title_attempts, self.config['supplemental_items'])
                item = apply_rules(item, self.config['supplemental_items'], readings)
                record = json.loads(Path(path).with_suffix('.json').read_text(encoding='utf-8'))
                record['supplemental'] = {'title_readings': readings, 'title_attempts': title_attempts,
                                          'name': item.name, 'rule': item.custom_rule,
                                          'action': item.action, 'reason': item.reason}
                Path(path).with_suffix('.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        if not item.quality and not item.custom_rule and not has_item_details(details, previous['lines']):
            return None
        item.image = path
        return item

    def enhance_header(self, frame, name):
        from tooltip_ocr import header_view, consensus
        view = header_view(frame)
        if view is None:
            return None
        results = []
        for scale in (4, 5):
            result, unused = self.read_saved(view, name + '-header-cubic', scale=scale, interpolation='cubic')
            results.append(result)
        return consensus(results)

    def rarity_lines(self, frame, name):
        known = header_qualities(frame)
        if known:
            return known
        x = min(56, frame.width // 3)
        boxes = [[x, 36, min(75, frame.width - x - 4), min(20, frame.height - 36)],
                 [min(48, frame.width // 4), 28,
                  frame.width - min(48, frame.width // 4) - 4, min(26, frame.height - 28)]]
        extras = []
        for box, scales in zip(boxes, ((3, 4), (4, 5))):
            if min(box[2:]) < 1:
                continue
            header = frame.crop(box).padded()
            exact = []
            for scale in scales:
                result, unused = self.read_saved(header, name + '-rarity', scale=scale)
                for line in result['lines']:
                    hit = re.fullmatch(r'[^\w\u4e00-\u9fff]*(' + '|'.join(QUALITY_NAMES + OTHER_QUALITIES)
                                       + r')(?:级|級|報|报)?', compact(line))
                    if hit:
                        exact.append(hit[1])
                    else:
                        extras.append(line)
            if exact:
                return exact + extras
        return extras

    def ensure_panels(self):
        from panels import ensure_panels
        ensure_panels(self)

    def collapse_panels(self):
        from panels import collapse_panels
        collapse_panels(self)

    def tooltip_level(self, frame, results, name):
        """Read digits after the required-level label, inside this tooltip only."""
        boxes = []
        for result in results:
            for line in result.get('details', []):
                words = line.get('words', [])
                text = ''.join(compact(w['text']) for w in words)
                label = re.search(REQUIRED_LEVEL_LABEL, text)
                if label is None or not words:
                    continue
                offset, x = 0, None
                for word in words:
                    length = len(compact(word['text']))
                    if length and offset + length >= label.end():
                        x = int(word['box'][0] + word['box'][2] * (label.end() - offset) / length)
                        break
                    offset += length
                if x is None:
                    continue
                y = max(0, int(min(w['box'][1] for w in words)) - 2)
                bottom = min(frame.height, int(max(w['box'][1] + w['box'][3] for w in words)) + 3)
                x = max(0, x - 1)
                if frame.width - x > 4 and bottom > y:
                    boxes.append([x, y, frame.width - x, bottom - y])
        for box in boxes[:2]:
            digits = frame.crop(box).padded()
            values = []
            for scale in (4, 5):
                result, unused = self.read_saved(digits, name + '-required-level', scale=scale, language='en')
                value = compact(result['text'])
                values.append(int(value) if re.fullmatch(r'[1-9]\d{0,2}', value) else None)
            if values[0] is not None and values[0] == values[1]:
                return values[0]
        return None

    def read_item(self, slot, baseline, key='inventory', point=None, *, read_quantity=True):
        if point is None:
            point = self.inventory_point(slot)
        # Reset hover per slot so a stale tooltip cannot be attributed to the
        # next item, then retry a missing tooltip with a longer dwell.
        self.neutral()
        if key == 'inventory':
            before = self.game.frame()
            if isinstance(before, Frame) and isinstance(getattr(before, 'pixels', None), bytes):
                self.baseline_frame = before
                box = cell_box(self.config, key, point)
                if before.crop(box).empty_inventory_slot():
                    self.game.wait(0.12)
                    after = self.game.frame()
                    if after.crop(box).empty_inventory_slot() and before.crop(box).matches(after.crop(box).template(), 6, 0.06):
                        return Item(slot, 'keep', '', '', None, '已确认空格，停止向后扫描', '',
                                    self.save_state(f'{key}-{slot:02d}-empty'), empty=True)
        delay = max(0.3, float(self.config.get('hover_seconds', 1.2)))
        self.game.hover(point, delay)
        path = self.save_state(f'{key}-{slot:02d}')
        restored = False

        def observe():
            nonlocal point, path, restored
            self._last_tooltip_selection = None
            result = self.local_item(slot, key, point)
            info = self.__dict__.get('_last_tooltip_selection')
            if not isinstance(info, dict):
                return result
            if info.get('method') == 'nearest':
                self._tooltip_restore_waiting_normal = False
                return result
            distant = any(candidate.get('framed_box') for candidate in info.get('candidates', []))
            if distant and not restored and not self.__dict__.get('_tooltip_restore_waiting_normal', False):
                from window_restore import restore_positions
                restored = True
                self._tooltip_restore_waiting_normal = True
                original = list(self.config['points'][key + '_first'])
                restore_positions(self)
                shifted = self.config['points'][key + '_first']
                point = [point[n] + shifted[n] - original[n] for n in (0, 1)]
                self.neutral()
                self.baseline_frame = self.game.frame()
                self.game.hover(point, delay)
                path = self.save_state(f'{key}-{slot:02d}-restored')
                self._last_tooltip_selection = None
                result = self.local_item(slot, key, point)
                if (self.__dict__.get('_last_tooltip_selection') or {}).get('method') == 'nearest':
                    self._tooltip_restore_waiting_normal = False
            return result

        item = observe()
        if item is None:
            self.note(f'{"背包" if key == "inventory" else "魔方"} {slot + 1}：未出现可识别提示框，延长悬停重试一次。')
            self.game.hover(point, max(1.5, delay * 2), retry=True)
            path = self.save_state(f'{key}-{slot:02d}-retry')
            retry = observe()
            item = retry if retry is not None else item
        if item is None:
            item = Item(slot, 'keep', '', '', None, '未检测到物品详情（空格或悬停未触发），保留', '', path)
            before = getattr(self, 'baseline_frame', None)
            if key == 'inventory' and isinstance(before, Frame):
                box = cell_box(self.config, key, point)
                self.neutral()
                if before.crop(box).empty_inventory_slot() and self.game.frame().crop(box).empty_inventory_slot():
                    item.empty, item.reason = True, '已确认空格，停止向后扫描'
            return item
        item.image = item.image or path
        if key == 'inventory':
            item.view_top = self.pager().top
        if item.kind == 'material' and read_quantity and item.action != 'store':
            item.count = self.read_stack_count(getattr(self, 'baseline_frame', None), key, point)
            if item.count is None and item.action != 'keep':
                item.reason += '；材料堆叠数量未确认，仅 Alt＋右键添加一次'
        return item

    def scan(self, on_item=None):
        pager = self.pager()
        self.note(f'开始读取背包：{pager.columns} 列、共 {pager.total_rows} 行，可见 {pager.rows} 行。')
        pager.reset()
        items = []
        last_read = -1
        vacancies = getattr(self, 'vacated_slots', set())
        finished = False
        while not finished:
            baseline = self.baseline()
            first_slot = max(last_read + 1, pager.top * pager.columns)
            end_slot = (pager.top + pager.rows) * pager.columns
            for slot in range(first_slot, end_slot):
                item = self.read_item(slot, baseline)
                item.view_top = pager.top
                if item.empty and slot in vacancies:
                    # These holes were created by our own accepted drags.
                    # They are not the end of the original packed inventory.
                    self.note(f'背包 {slot + 1}：本次拖出后留下的空格，继续寻找清单中的物品。')
                    last_read = slot
                    continue
                if not item.empty:
                    vacancies.discard(slot)
                items.append(item)
                last_read = slot
                self.progress(list(items))
                self.note(f'背包 {slot + 1}：{item.action} / {item.quality or "未知"} / {item.reason}')
                if not item.empty and on_item is not None and on_item(item):
                    finished = True
                    break
                if item.empty:
                    self.note(f'在第 {slot // pager.columns + 1} 行、第 {slot % pager.columns + 1} 列遇到空格，结束扫描。')
                    finished = True
                    break
            if finished or pager.top + pager.rows >= pager.total_rows:
                break
            pager.advance()
        self.neutral()
        (self.folder / 'inventory.json').write_text(
            json.dumps([i.to_dict() for i in items], ensure_ascii=False, indent=2), encoding='utf-8')
        if items and not any(i.empty or i.custom_rule or has_item_details(i.text.splitlines()) for i in items):
            raise RuntimeError('本页未检测到任何物品详情，不能生成执行清单。请先使用“测试第一格”检查悬停；'
                               f'若第一格为空，请暂时放入一个物品后测试。截图：{self.folder}')
        return items

    def test_hover(self):
        self.pager().reset()
        baseline = self.baseline()
        item = self.read_item(0, baseline)
        self.progress([item])
        self.note(f'第一格悬停测试：{item.reason}；截图：{item.image}')
        self.neutral()
        return item

    def board_points(self):
        return grid(self.config, 'cube', 3, 3)

    def occupied(self, mode, frame=None):
        if frame is None:
            frame = self.game.frame()
        refs = self.config['learned'][mode]['empty_board']
        return [n for n, p in enumerate(self.board_points())
                if not frame.crop(cell_box(self.config, 'cube', p)).matches(refs[n])]

    def settled_board(self, mode, allowed, timeout=8):
        """Observe transitions without treating one changed pixel frame as an item."""
        deadline = time.monotonic() + timeout
        previous = None
        stable = 0
        warned = False
        observations = []
        while time.monotonic() < deadline:
            frame = self.game.frame()
            occupied = self.occupied(mode, frame=frame)
            unexpected = sorted(set(occupied) - set(allowed))
            cells = [frame.crop(cell_box(self.config, 'cube', p)) for p in self.board_points()]
            observations.append({'occupied': occupied, 'unexpected': unexpected})
            if unexpected:
                stable = 0
                previous = None
                if not warned:
                    self.note('腐蚀后画面尚未稳定，等待九宫格变化结束；临时变化格：'
                              + '、'.join(str(n + 1) for n in unexpected) + '。')
                    warned = True
            else:
                if previous is not None and occupied == previous[0] and same_cells(previous[1], cells):
                    stable += 1
                else:
                    stable = 1
                previous = occupied, cells
                if stable >= 3:
                    return frame, occupied
            self.game.wait(0.25)
        self.serial += 1
        path = self.folder / f'{self.serial:05d}-cube-unsettled.bmp'
        frame.save(path)
        path.with_suffix('.json').write_text(json.dumps({
            'allowed': list(allowed), 'observations': observations,
            'boxes': [cell_box(self.config, 'cube', p) for p in self.board_points()],
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        actual = '、'.join(str(n + 1) for n in observations[-1]['unexpected']) or '无，画面仍变化'
        raise RuntimeError(f'等待九宫格稳定超时，持续异常格：{actual}。已停止，不会重复点击。截图：{path}')

    def check_options(self, mode):
        frame = self.game.frame()
        for key in ['options_' + mode] + (['recipe_synthesize'] if mode == 'synthesize' else []):
            if not frame.crop(self.config['regions'][key]).matches(self.config['learned'][mode][key], 5, 0.03):
                raise RuntimeError('配方或复选框状态与校准记录不同，请手动恢复设置后重试。')

    def mode(self, wanted):
        if wanted not in ('corrode', 'synthesize'):
            raise ValueError('未知魔方目标功能。')
        self.neutral()
        label_frame = self.game.frame().crop(self.config['regions']['mode_label'])
        matches = [mode for mode in ('corrode', 'synthesize') if label_frame.matches(
            self.config['learned'][mode]['mode_label'], 1, 0.01)]
        current = matches[0] if len(matches) == 1 else None
        if current is not None and self.occupied(current):
            raise RuntimeError('魔方九宫格中已有物品，请先手动取出。')
        if current is None:
            from cube_modes import select_unknown
            select_unknown(self, wanted)
        elif current != wanted:
            self.game.click(self.config['points']['mode_menu'])
            self.game.wait(0.4)
            self.game.click(self.config['points']['mode_' + wanted])
            self.neutral()
        label = '腐蚀' if wanted == 'corrode' else '合成'
        for unused in range(8):
            label_frame = self.game.frame().crop(self.config['regions']['mode_label'])
            confirmed = [mode for mode in ('corrode', 'synthesize') if label_frame.matches(
                self.config['learned'][mode]['mode_label'], 1, 0.01)]
            if confirmed == [wanted]:
                break
            self.game.wait(0.25)
        else:
            path = self.save_state('mode-switch-unconfirmed')
            raise RuntimeError('功能切换未得到确认，已停止。截图：' + path)
        self.check_options(wanted)
        if self.occupied(wanted):
            raise RuntimeError('九宫格非空，已停止。')
        self.note('已确认功能：' + label)
        self.current_mode = wanted

    def empty_warehouse(self):
        self.neutral()
        frame = self.game.frame()
        template = self.config['learned']['synthesize']['empty_warehouse']
        for point in grid(self.config, 'warehouse', self.config['warehouse_columns'], self.config['warehouse_rows']):
            if frame.crop(cell_box(self.config, 'warehouse', point)).matches(template, 5, 0.025):
                return point
        raise RuntimeError('当前仓库页没有可确认的空位，已停止。')

    def return_to_inventory(self, *, slots=None, allowed=None):
        """Return selected inputs, leaving other known batch slots untouched."""
        self.neutral()
        frame = self.game.frame()
        matches = [mode for mode in ('corrode', 'synthesize') if frame.crop(
            self.config['regions']['mode_label']).matches(self.config['learned'][mode]['mode_label'], 1, 0.01)]
        if len(matches) != 1:
            raise RuntimeError('无法确认当前魔方功能，未取回物品。')
        mode = matches[0]
        unused, remaining = self.settled_board(mode, list(range(9)))
        if allowed is not None and set(remaining) - set(allowed):
            path = self.save_state('cube-return-unknown')
            raise RuntimeError('魔方存在本批记录之外的槽位，未自动取回，请手动右键取回后继续。截图：' + path)
        selected = [n for n in remaining if slots is None or n in slots]
        count = len(selected)
        for index in selected:
            self.game.right_click(self.board_points()[index])
            self.neutral()
            expected = [n for n in remaining if n != index]
            unused, actual = self.settled_board(mode, list(range(9)))
            if actual != expected:
                raise RuntimeError('右键取回未确认，可能背包已满，已停止。')
            remaining = expected
        if slots is None:
            self.vacated_slots = set()
        self._stack_cache = {}
        self.baseline_frame = None
        self.note(f'已右键取回魔方中的 {count} 格物品。')

    def read_source_count(self, item, verify_identity=True):
        self.show_inventory_item(item)
        if verify_identity:
            baseline = self.baseline()
            current = self.read_item(item.slot, baseline)
            self.neutral()
            if current.empty:
                return 0
            return current.count if current.identity() == item.identity() else None
        self.neutral()
        frame = self.game.frame()
        point = self.inventory_point(item.slot)
        if isinstance(frame, Frame) and frame.crop(cell_box(self.config, 'inventory', point)).empty_inventory_slot():
            return 0
        return self.read_stack_count(frame, 'inventory', point)

    def action_allowed(self, mode, item):
        if item.custom_rule:
            from supplemental import allowed
            return allowed(item, self.config.get('supplemental_items'), mode)
        if mode == 'store':
            return item.action == 'store' and (item.quality in WAREHOUSE_QUALITIES
                    or (item.kind == 'material' and (item.material_type == 'offering'
                        or (item.material_type == 'inscription' and item.quality in INSCRIPTION_STORAGE_QUALITIES))))
        selected = selected_for_corrosion(item, self.config.get('corrosion_selection'))
        if mode == 'corrode':
            return item.action == mode and selected
        return (mode == 'synthesize' and item.action == mode and not selected
                and item.quality in SYNTHESIS_QUALITIES and item.kind == 'equipment')

    def require_action(self, mode, item):
        if not self.action_allowed(mode, item):
            raise RuntimeError('物品与当前腐蚀勾选或合成规则不一致，已停止，未执行。')

    def require_synthesis_batch(self, batch):
        if len(batch) != 9 or len({item.quality for item in batch}) != 1:
            raise RuntimeError('合成必须为同品质的九件传奇或不朽装备，未执行。')
        for item in batch:
            self.require_action('synthesize', item)

    def warehouse_cells(self, frame):
        return [frame.crop(cell_box(self.config, 'warehouse', p)) for p in grid(
            self.config, 'warehouse', self.config['warehouse_columns'], self.config['warehouse_rows'])]

    def store_item(self, expected, *, recognized=False):
        self.require_action('store', expected)
        self.show_inventory_item(expected)
        current = expected if recognized else self.read_item(expected.slot, self.baseline())
        if current.identity() != expected.identity():
            raise RuntimeError('入库前物品发生变化，请重新开始收菜。')
        self.require_action('store', current)
        result = self.store_current_page(current)
        if result is not StorageResult.FULL:
            return result
        pager = WarehousePager(self)
        count = len(pager.pages.points)
        for page in range(pager.pages.current + 1, count + 1):
            pager.select(page, current)
            result = self.store_current_page(current)
            if result is not StorageResult.FULL:
                return result
        self.note('已到仓库最后一页，从第一页开始复查本次物品可用容量。')
        for page in range(1, count + 1):
            pager.select(page, current)
            result = self.store_current_page(current)
            if result is not StorageResult.FULL:
                return result
        self.note(f'仓库全部 {count} 页均无法接收，背包 {current.slot + 1} 保留，跳过本次入库。')
        return StorageResult.FULL

    def store_current_page(self, current):
        # Ctrl+right click can merge into an existing warehouse stack even
        # when the current page has no empty slot. Confirm the actual transfer.
        self.neutral()
        before = self.game.frame()
        previous_cells = self.warehouse_cells(before)
        mode = getattr(self, 'current_mode', 'corrode')
        board_before = self.occupied(mode, frame=before)
        point = self.inventory_point(current.slot)
        source_box = cell_box(self.config, 'inventory', point)
        source_before = before.crop(source_box)
        was_full = all(not cell.empty_inventory_slot() for cell in previous_cells)
        self.game.right_click(point, ctrl=True)
        self.neutral()
        deadline = time.monotonic() + 8
        stable, previous = 0, None
        misplaced_stable, misplaced_previous = 0, None
        full_stable = 0
        receipt_stable, receipt_previous = 0, None
        core = source_before.crop([source_before.width // 5, source_before.height // 6,
                                   source_before.width * 3 // 5, source_before.height // 2])
        diagnostics = {}
        while time.monotonic() < deadline:
            frame = self.game.frame()
            cells = self.warehouse_cells(frame)
            board_after = self.occupied(mode, frame=frame)
            extra = sorted(set(board_after) - set(board_before))
            if extra:
                misplaced_stable = misplaced_stable + 1 if extra == misplaced_previous else 1
                misplaced_previous = extra
                if misplaced_stable >= 3:
                    path = self.save_state('warehouse-shortcut-wrong-target')
                    self.return_to_inventory(slots=set(extra), allowed=set(board_after))
                    self.note('Ctrl＋右键后检测到物品误入魔方，已右键取回并跳过；未执行腐蚀。截图：' + path)
                    return False
            else:
                misplaced_stable, misplaced_previous = 0, None
            source_empty = frame.crop(cell_box(self.config, 'inventory', point)).empty_inventory_slot()
            changed = not same_warehouse_cells(previous_cells, cells)
            # Other warehouse icons may animate continuously. Confirm the
            # receiving cell separately when it changed and shows this source
            # sprite, without letting unrelated animations reset the receipt.
            receipts = [index for index, (old, now) in enumerate(zip(previous_cells, cells))
                        if not same_warehouse_cells([old], [now])
                        and find_matches(now, core, mean_limit=8, changed_limit=.06)] if source_empty else []
            receipt = [(index, cells[index]) for index in receipts]
            receipt_stable = receipt_stable + 1 if (receipt and receipt_previous
                and [i for i, cell in receipt] == [i for i, cell in receipt_previous]
                and same_warehouse_cells([cell for i, cell in receipt],
                                         [cell for i, cell in receipt_previous])) else 1 if receipt else 0
            receipt_previous = receipt
            diagnostics = {'slot': current.slot, 'point': point, 'source_empty': source_empty,
                           'warehouse_changed': changed, 'receiving_cells': receipts,
                           'receiving_stable_frames': receipt_stable, 'cube_before': board_before,
                           'cube_after': board_after, 'warehouse_was_full': was_full}
            if (was_full and not source_empty and not changed and board_after == board_before
                    and frame.crop(source_box).matches(source_before.template(), 3, .02)
                    and all(not cell.empty_inventory_slot() for cell in cells)):
                full_stable += 1
                if full_stable >= 5:
                    self.note('当前仓库页无空格，尝试堆叠后未转移，检查其他仓库页。')
                    return StorageResult.FULL
            else:
                full_stable = 0
            if source_empty and changed and board_after == board_before:
                stable = stable + 1 if previous is not None and same_warehouse_cells(previous, cells) else 1
                if stable >= 3 or receipt_stable >= 3:
                    self.vacated_slots = getattr(self, 'vacated_slots', set()) | {current.slot}
                    self._stack_cache = {}
                    label = '供奉材料' if current.material_type == 'offering' else current.quality + '物品'
                    self.note(f'背包 {current.slot + 1}：{label}已 Ctrl＋右键入库。')
                    return True
                previous = cells
            else:
                stable, previous = 0, None
            self.game.wait(0.25)
        path = self.save_state('warehouse-transfer-unconfirmed')
        if Path(path).is_file():
            Path(path).with_suffix('.json').write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding='utf-8')
        self.note('入库确认状态：' + json.dumps(diagnostics, ensure_ascii=False))
        raise RuntimeError('Ctrl＋右键后未确认背包原格清空及仓库物品或堆叠数量变化，请检查仓库容量或弹窗；'
                           '已暂停，不重复点击。截图：' + path)

    def transfer(self, mode, batch):
        if mode == 'synthesize':
            self.require_synthesis_batch(batch)
        for item in batch:
            self.require_action(mode, item)
        for index, expected in enumerate(batch):
            self.show_inventory_item(expected)
            baseline = self.baseline()
            current = self.read_item(expected.slot, baseline)
            if current.identity() != expected.identity():
                current = next((i for i in self.scan() if i.identity() == expected.identity()), None)
                if current is None:
                    raise RuntimeError('背包物品发生变化或复核不一致，已停止。')
                self.show_inventory_item(current)
                baseline = self.baseline()
                checked = self.read_item(current.slot, baseline)
                if checked.identity() != expected.identity():
                    raise RuntimeError('滚动定位后物品复核不一致，已停止。')
                current = checked
            self.require_action(mode, current)
            self.neutral()
            self.game.right_click(self.inventory_point(current.slot), alt=True)
            self.neutral()
            if self.occupied(mode) != list(range(index + 1)):
                raise RuntimeError('Alt＋右键添加结果不符合预期，已停止，请检查九宫格或数量弹窗。')
            location = 'cube'
            single_unconfirmed = mode == 'corrode' and current.kind == 'material' and current.count is None
            if single_unconfirmed:
                moved, location = 1, 'single_unconfirmed'
            elif current.kind == 'material':
                source_after = self.read_source_count(current, verify_identity=False)
                if source_after is None:
                    raise RuntimeError('添加后背包材料数量未确认，尚未执行腐蚀。')
                if source_after == current.count:
                    # The cube references a stack still in the backpack.
                    moved, location = current.count, 'inventory'
                else:
                    moved = self.read_stack_count(self.game.frame(), 'cube', self.board_points()[index])
                    if moved is not None and current.count - source_after != moved:
                        raise RuntimeError('添加前后背包与魔方数量不一致，尚未执行腐蚀。')
            else:
                moved = 1
            maximum = 1 if single_unconfirmed else expected.count
            if moved is None or maximum is None or not 1 <= moved <= maximum:
                raise RuntimeError('添加材料数量未确认或超过本次清单，尚未执行腐蚀，请检查九宫格。')
            batch[index] = replace(expected, slot=current.slot, view_top=current.view_top,
                                   count=moved, count_location=location)
            self.note(f'背包 {current.slot + 1} → 魔方 {index + 1}：选中 {moved} 个；'
                      + ('执行后按背包剩余数量复核。' if location == 'inventory' else '按魔方数量复核。'))
            if not hasattr(self, 'vacated_slots'):
                self.vacated_slots = set()
            if current.count is not None and moved >= current.count and location == 'cube':
                self.vacated_slots.add(current.slot)

    def verify_board(self, mode, batch, positions=None):
        sources = {}
        for index, expected in zip(range(len(batch)) if positions is None else positions, batch):
            self.require_action(mode, expected)
            baseline = self.baseline()
            if expected.count_location in ('inventory', 'inventory_units', 'single_unconfirmed'):
                current = self.read_item(index, baseline, 'cube', self.board_points()[index], read_quantity=False)
            else:
                current = self.read_item(index, baseline, 'cube', self.board_points()[index])
            if (not self.action_allowed(mode, current) or current.quality != expected.quality
                    or current.kind != expected.kind):
                labels = {'equipment': '装备', 'material': '材料', 'corrode': '腐蚀', 'synthesize': '合成',
                          'store': '入库', 'keep': '保留', '': '未知'}
                detail = (f'第 {index + 1} 格：预期 {expected.quality}/{labels[expected.kind]}/{labels[mode]}；'
                          f'读到 {current.quality or "未知"}/{labels[current.kind]}/{labels[current.action]}；'
                          f'{current.reason}。截图：{current.image}')
                self.note('九宫格物品复核失败，' + detail)
                raise RuntimeError('九宫格物品复核失败，未执行腐蚀或合成。\n' + detail
                                   + '\n请先用“取回魔方物品”或手动右键取回，再重新开始收菜。')
            wanted = expected.count
            if expected.count_location == 'inventory_units':
                source_key = (expected.slot, expected.view_top, expected.identity())
                if source_key not in sources:
                    sources[source_key] = self.read_source_count(expected)
                count, wanted = sources[source_key], expected.source_count
            else:
                count = self.read_source_count(expected) if expected.count_location == 'inventory' else current.count
            if count is None or count != wanted:
                raise RuntimeError(f'魔方第 {index + 1} 格数量复核不一致：预期 {wanted}，读到 {count}，未执行。')
        self.neutral()
        self.check_options(mode)

    def wait_corrode_progress(self, batch, counts, *, fast=False):
        unit_groups = {}
        for index, old in enumerate(counts):
            if old and batch[index].count_location == 'inventory_units':
                item = batch[index]
                unit_groups.setdefault((item.slot, item.view_top, item.identity()), []).append(index)
        for indices in unit_groups.values():
            baselines = {batch[n].source_count for n in indices}
            if (len(baselines) != 1 or any(counts[n] != 1 for n in indices)
                    or type(batch[indices[0]].source_count) is not int
                    or not len(indices) <= batch[indices[0]].source_count <= 5):
                raise RuntimeError('同一叠材料的投料数量记录不一致，未重复执行。')
        deadline = time.monotonic() + max(15, float(self.config.get('result_timeout_seconds', 30)))
        while time.monotonic() < deadline:
            allowed = [n for n, count in enumerate(counts) if count]
            frame, occupied = self.settled_board('corrode', allowed)
            current, consumed = [0] * len(counts), [0] * len(counts)
            known = True
            for index, old in enumerate(counts):
                if not old:
                    continue
                if batch[index].count_location == 'inventory_units':
                    continue  # A shared stack must be read/debited once below.
                if batch[index].count_location == 'inventory':
                    new = self.read_source_count(batch[index], verify_identity=False) if fast else self.read_source_count(batch[index])
                elif index not in occupied:
                    new = 0
                elif batch[index].count_location == 'single_unconfirmed':
                    new = 1  # Only a cleared selection proves this single submission finished.
                else:
                    new = self.read_stack_count(frame, 'cube', self.board_points()[index]) if batch[index].kind == 'material' else 1
                if new is None:
                    known = False
                    continue
                if new > old or new < old - 1:
                    raise RuntimeError('腐蚀后的数量变化不符合每次消耗 1 个的规则，已停止。')
                consumed[index] = old - new
                # If the cube cleared its selection after consuming one unit,
                # leave the rest in the backpack for a later approved drag.
                current[index] = new
            source_readings = []
            for indices in unit_groups.values():
                first = batch[indices[0]]
                new = self.read_source_count(first, verify_identity=not fast)
                if new is None:
                    known = False
                    continue
                if type(new) is not int or new < 0:
                    raise RuntimeError('腐蚀后的材料数量无法确认，已停止。')
                delta = first.source_count - new
                if delta < 0 or delta > len(indices):
                    raise RuntimeError('腐蚀后的同一叠材料数量变化超过本批已添加数量，已停止。')
                source_readings.append((indices, new, delta))
            # Refresh after ALL source reads; a tooltip may outlast a cleared
            # selection. All entries for one stack share a single decrement.
            unused, latest = self.settled_board('corrode', allowed)
            source_updates = []
            for indices, new, delta in source_readings:
                cleared = [n for n in indices if n not in latest]
                if delta != len(cleared):
                    known = False
                    if not latest:
                        for n in indices[:delta]:
                            consumed[n] = 1
                    continue
                for n in indices:
                    current[n] = 0 if n in cleared else 1
                    consumed[n] = 1 if n in cleared else 0
                source_updates.append((indices, new))
            if not latest and sum(consumed) > 0:
                # The submitted selection is gone and consumption is proven.
                # A material source can remain unchanged or unreadable (for
                # example after loot joins that stack). Do not replay this
                # completed batch or invent a decrement for that material.
                uncertain = [str(n + 1) for n, old in enumerate(counts) if old and not consumed[n]]
                if uncertain:
                    self.note('九宫格已清空，部分物品消耗已确认；魔方第 ' + '、'.join(uncertain)
                              + ' 格未确认消耗，剩余物品整理后重新扫描，不重复点击本批。')
                return [0] * len(counts), consumed
            if known:
                for index, old in enumerate(counts):
                    if not old:
                        continue
                    if index in latest and current[index] == 0:
                        known = False  # The selection is still animating out.
                    elif index not in latest:
                        if consumed[index] == 0 or (batch[index].count_location == 'cube' and current[index] > 0):
                            known = False
                        current[index] = 0
            if known and sum(consumed) > 0:
                for indices, new in source_updates:
                    for n in indices:
                        batch[n].source_count = new
                return current, consumed
            self.game.wait(0.5)
        path = self.save_state('corrode-no-progress')
        raise RuntimeError('腐蚀点击后未确认数量减少，已停止，不会重复点击。请检查按钮或弹窗。截图：' + path)

    def corrode_batch(self, batch):
        counts = [item.count for item in batch]
        total_consumed = [0] * len(batch)
        if any(count is None or not 1 <= count <= 5 for count in counts):
            raise RuntimeError('腐蚀材料数量未确认，未执行。')
        # At most one unit per occupied slot may disappear per click. A click
        # that made no confirmed progress must never trigger another click.
        for round_index in range(sum(counts)):
            if not any(counts):
                batch[:] = [replace(item, count=used) for item, used in zip(batch, total_consumed)]
                self.note('腐蚀完成，九宫格已清空。')
                return
            positions = [n for n, count in enumerate(counts) if count]
            if self.occupied('corrode') != positions:
                raise RuntimeError('腐蚀前九宫格状态发生变化，未执行。')
            if round_index:
                self.verify_board('corrode', [replace(batch[n], count=counts[n]) for n in positions], positions)
            self.game.wait(max(0.5, float(self.config.get('action_interval_seconds', 0.8))))
            self.check_options('corrode')
            if self.occupied('corrode') != positions:
                raise RuntimeError('等待期间九宫格状态发生变化，未执行。')
            self.note(f'腐蚀第 {round_index + 1} 次：当前共 {sum(counts)} 个，点击后等待数量变化。')
            self.game.click(self.config['points']['commit_corrode'])
            self.neutral()
            counts, consumed = self.wait_corrode_progress(batch, counts)
            total_consumed = [a + b for a, b in zip(total_consumed, consumed)]
            self.note(f'已确认消耗 {sum(consumed)} 个，本批魔方仍选中 {sum(counts)} 个；背包余量会继续按清单处理。')
        if any(counts):
            raise RuntimeError('达到本批材料数量上限，仍有未确认物品，已停止。')
        batch[:] = [replace(item, count=used) for item, used in zip(batch, total_consumed)]
        self.note('腐蚀完成，九宫格已清空。')

    def commit(self, mode, batch):
        if not 1 <= len(batch) <= 9 or (mode == 'synthesize' and len(batch) != 9):
            raise RuntimeError('投入数量不符合配方要求，未执行。')
        if mode == 'synthesize':
            self.require_synthesis_batch(batch)
        if self.occupied(mode) != list(range(len(batch))):
            raise RuntimeError('九宫格状态发生变化，未执行。')
        self.verify_board(mode, batch)
        if mode == 'corrode':
            self.corrode_batch(batch)
            return
        self.note(f'执行 {mode}，数量 {len(batch)}。')
        self.game.wait(max(0.5, float(self.config.get('action_interval_seconds', 0.8))))
        self.game.click(self.config['points']['commit_' + mode])
        self.neutral()
        self.wait_synthesis_output()
        self.game.right_click(self.board_points()[4])
        self.neutral()
        self.confirm_synthesis_return()

    def wait_synthesis_output(self):
        """Only the stable center output confirms a completed synthesis."""
        deadline = time.monotonic() + max(15, float(self.config.get('result_timeout_seconds', 30)))
        while time.monotonic() < deadline:
            unused, occupied = self.settled_board('synthesize', list(range(9)))
            if occupied == [4]:
                return 4
            self.game.wait(0.25)
        raise RuntimeError('合成结果尚未在九宫格中间确认，请处理游戏弹窗后继续；不会重复点击合成。')

    def confirm_synthesis_return(self):
        unused, occupied = self.settled_board('synthesize', list(range(9)))
        if occupied:
            raise RuntimeError('合成产物右键取回背包尚未确认，请检查背包空位或游戏弹窗后继续。')
        self._stack_cache = {}
        self.baseline_frame = None
        self.note('合成产物已右键收回背包，九宫格已清空。')

    def sort_inventory(self, mode='corrode'):
        self.neutral()
        if self.occupied(mode):
            raise RuntimeError('九宫格尚未清空，未点击背包整理。')
        self.check_options(mode)
        point = sort_button(self.game.frame(), self.config)
        self.note(f'点击背包整理按钮：{point}。')
        self.game.click(point)
        self.neutral()
        self.game.wait(max(0.5, float(self.config.get('action_interval_seconds', 0.8))))
        pager = self.pager()
        previous, stable = pager.snapshot(), 0
        for unused in range(20):
            self.game.wait(0.25)
            current = pager.snapshot()
            stable = stable + 1 if same_cells(previous, current) else 0
            if stable >= 2:
                break
            previous = current
        else:
            raise RuntimeError('背包整理后物品位置尚未稳定，已停止。')
        pager.reset()
        vacancy = False
        for cell in pager.snapshot():
            if cell.empty_inventory_slot():
                vacancy = True
            elif vacancy:
                raise RuntimeError('背包整理后可见区域仍有空格夹在物品中间，已停止，请确认整理按钮是否生效。')
        # Sorting repacks holes and can merge stacks. Every old position and
        # tooltip/count cache is invalid; the next scan resets to the top.
        self.vacated_slots = set()
        self._stack_cache = {}
        self.baseline_frame = None
        self.note('背包整理完成，下一轮从顶部重新扫描。')

    def execute(self, preview):
        corroded = synthesized = stored = 0
        storage_skipped = set()
        self.ensure_panels()
        self.mode('corrode')
        self.neutral()
        sort_button(self.game.frame(), self.config)  # Validate before consuming anything.
        while True:
            items = self.scan()
            storage = [item for item in items if self.action_allowed('store', item) and item.identity() not in storage_skipped]
            if storage:
                result = self.store_item(storage[0])
                if result is False:
                    raise RuntimeError('高品质入库快捷键结果异常，物品已取回，未执行腐蚀。')
                if result is StorageResult.FULL:
                    storage_skipped.add(storage[0].identity())
                else:
                    stored += 1
                    self.sort_inventory()
                continue
            eligible = [item for item in items if self.action_allowed('corrode', item)]
            plans = [batch for action, batch in batches(eligible) if action == 'corrode']
            if not plans:
                uncertain = [i for i in items if not i.empty and (not i.quality or not i.kind or i.count is None)]
                self.note('背包已无可确认用于腐蚀的物品，结束腐蚀循环。'
                          + (f'仍有 {len(uncertain)} 格识别不完整，已保留。' if uncertain else ''))
                break
            batch = plans[0]
            self.transfer('corrode', batch)
            self.commit('corrode', batch)
            if not any(item.count for item in batch):
                raise RuntimeError('本批未确认腐蚀消耗，已停止循环。')
            corroded += 1
            self.sort_inventory()
        # Use the final corrosion scan, then refresh after each returned output.
        # New immortal equipment produced by legendary synthesis can participate.
        if any(action == 'synthesize' for action, batch in batches(items)):
            self.mode('synthesize')
            while synthesized < self.config['max_batches']:
                storage = [item for item in items if self.action_allowed('store', item) and item.identity() not in storage_skipped]
                if storage:
                    result = self.store_item(storage[0])
                    if result is False:
                        raise RuntimeError('高品质入库快捷键结果异常，物品已取回，未执行合成。')
                    if result is StorageResult.FULL:
                        storage_skipped.add(storage[0].identity())
                    else:
                        stored += 1
                        self.sort_inventory('synthesize')
                    items = self.scan()
                    continue
                plans = [batch for action, batch in batches(items) if action == 'synthesize'
                         and all(self.action_allowed('synthesize', item) for item in batch)]
                if not plans:
                    break
                batch = plans[0]
                self.transfer('synthesize', batch)
                self.commit('synthesize', batch)
                synthesized += 1
                self.sort_inventory('synthesize')
                if synthesized < self.config['max_batches']:
                    items = self.scan()
            if synthesized >= self.config['max_batches']:
                self.note('已达到合成本次批次数上限。')
        self.note(f'本次结束，入库 {stored} 格、腐蚀 {corroded} 批、合成 {synthesized} 批。识别不确定的物品仍保留。')

    def execute_stream(self, on_pause=None):
        from streaming import StreamRunner
        return StreamRunner(self, on_pause).run()


def learn(config, mode, stop):
    validate(config, execution=False)
    for key in POINT_LABELS:
        if key == 'inventory_sort':
            continue
        if key not in config['points']:
            raise RuntimeError('请完成所有点位校准：' + POINT_LABELS[key])
    required_regions = ['mode_label', 'options_' + mode] + (['recipe_synthesize'] if mode == 'synthesize' else [])
    for key in required_regions:
        if key not in config['regions']:
            raise RuntimeError('请完成区域校准：' + REGION_LABELS[key])
    engine = Engine(config, stop, lambda text: None)
    try:
        engine.neutral()
        frame = engine.game.frame()
        learned = {'empty_board': [frame.crop(cell_box(config, 'cube', p)).template() for p in engine.board_points()]}
        mode_label = frame.crop(config['regions']['mode_label'])
        other = 'synthesize' if mode == 'corrode' else 'corrode'
        if other in config['learned'] and mode_label.matches(config['learned'][other]['mode_label'], 1, 0.01):
            raise RuntimeError('两个模式的功能名称图像相同，请确认已切换模式并框选准确的功能名称区域。')
        learned['mode_label'] = mode_label.template()
        for key in required_regions[1:]:
            learned[key] = frame.crop(config['regions'][key]).template()
        if mode == 'synthesize':
            learned['empty_warehouse'] = frame.crop(cell_box(config, 'warehouse', config['points']['warehouse_empty'])).template()
        return learned
    finally:
        engine.close()
