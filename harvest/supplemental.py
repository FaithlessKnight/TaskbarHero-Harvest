"""Exact title rules supplied by the user; independent of Windows input."""
from dataclasses import replace
import re
import uuid

from rules import compact, QUALITY_NAMES, OTHER_QUALITIES, storage_rarity_line


QUALITIES = QUALITY_NAMES + OTHER_QUALITIES


def normalize_rules(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 1000:
        raise ValueError('补充物品必须是列表，最多 1000 条。')
    rules, ids = [], set()
    for row in value:
        if not isinstance(row, dict) or set(row) != {'id', 'name', 'kind', 'action', 'qualities'}:
            raise ValueError('补充物品字段无效。')
        name, identifier = row['name'], row['id']
        if (not isinstance(name, str) or not 1 <= len(compact(name)) <= 80
                or any(ord(c) < 32 for c in name)):
            raise ValueError('请输入完整物品名称，长度为 1～80 字。')
        if (not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', identifier)
                or identifier in ids):
            raise ValueError('补充物品编号无效或重复。')
        if row['kind'] not in ('equipment', 'material') or row['action'] not in ('corrode', 'store'):
            raise ValueError('补充物品的类型或处理方式无效。')
        qualities = row['qualities']
        if not isinstance(qualities, list) or any(q not in QUALITIES for q in qualities):
            raise ValueError('补充物品的品质范围无效。')
        rule = dict(row, name=compact(name), qualities=[q for q in QUALITIES if q in qualities])
        for existing in rules:
            if existing['name'] == rule['name'] and (not existing['qualities'] or not rule['qualities']
                    or set(existing['qualities']) & set(rule['qualities'])):
                raise ValueError('同名物品的品质范围重叠，请编辑已有规则或划分不同品质。')
        ids.add(identifier)
        rules.append(rule)
    return rules


def new_rule(name, kind, action, qualities=()):
    return normalize_rules([{'id': uuid.uuid4().hex, 'name': name, 'kind': kind,
                             'action': action, 'qualities': list(qualities)}])[0]


def title_text(value):
    value = compact(value)
    if not value or len(value) > 80:
        return ''
    if value in QUALITIES or value.rstrip('级級報报') in QUALITIES:
        return ''
    if value in ('固有属性', '特殊属性', '制作材料', '装饰材料', '雕刻材料', '铭刻材料', '供奉材料'):
        return ''
    return value


def title_from_details(result):
    # Only words wholly inside the selected tooltip title; rarity starts
    # below this band. Lower descriptions and neighbouring panels cannot match.
    names = []
    for line in result.get('details', []):
        words = line.get('words', [])
        if not words:
            continue
        boxes = [word.get('box', []) for word in words]
        if any(len(box) != 4 or box[0] < 4 or box[1] < 4 or box[2] <= 0 or box[3] <= 0
               or box[1] + box[3] > 34 for box in boxes):
            continue
        name = title_text(line.get('text', ''))
        if name:
            names.append(name)
    return names[0] if len(names) == 1 else ''


def title_from_crop(result):
    lines = [title_text(line) for line in result.get('lines', []) if compact(line)]
    return lines[0] if len(lines) == 1 and lines[0] else ''


def title_view(frame):
    """Exclude the decorative frame before isolating colored title strokes."""
    scale = (frame.width - 8) / 226
    if not .7 <= scale <= 1.25:
        return None
    x, y, height = round(14 * scale), round(15 * scale), round(18 * scale)
    if frame.height < y + height or frame.width <= x * 2:
        return None
    return frame.crop([x, y, frame.width - x * 2, height])


def resolved_readings(attempts, rules):
    registered = {row['name'] for row in rules}
    confirmed = {pair[0] for pair in attempts if len(pair) == 2 and pair[0]
                 and pair[0] == pair[1] and pair[0] in registered}
    if len(confirmed) == 1:
        return [next(iter(confirmed))] * 2
    if len(confirmed) > 1:
        return sorted(confirmed)
    # Preserve any exact but unrepeated registered reading so apply_rules
    # cannot silently fall through to a conflicting default action.
    singles = [name for pair in attempts for name in pair if name in registered]
    if singles:
        return [singles[0], '']
    return attempts[-1] if attempts else ['', '']


def apply_rules(item, rules, readings):
    rules = normalize_rules(rules)
    names = [name for name in readings if name]
    registered = {row['name'] for row in rules}
    matching_read = set(names) & registered
    if len(readings) < 2 or not readings[0] or any(name != readings[0] for name in readings):
        if matching_read:
            return replace(item, action='keep', custom_rule='', reason='补充物品名称两次识别不一致，保留')
        return item
    name = readings[0]
    item = replace(item, name=name)
    candidates = [row for row in rules if row['name'] == name
                  and (not row['qualities'] or item.quality in row['qualities'])]
    if not candidates:
        return item
    observed = {q for line in item.text.splitlines()[:12] for q in QUALITIES
                if compact(line) in (q, q + '级', q + '級', q + '報', q + '报')
                or storage_rarity_line(line) == q}
    missing_only = item.reason == '品质缺失或冲突，保留' and not observed
    if (len(candidates) != 1 or len(observed) > 1 or any(word in item.reason for word in ('锁定', '对比'))
            or ('冲突' in item.reason and not missing_only)):
        return replace(item, action='keep', custom_rule='', reason='补充物品存在锁定、对比或识别冲突，保留')
    if any(word in compact(item.text).upper() for word in ('已锁定', '已鎖定', '解除锁定', '解除鎖定',
                                                         '已装备', '已裝備', 'EQUIPPED')):
        return replace(item, action='keep', custom_rule='', reason='锁定或已装备对比物品，保留')
    rule = candidates[0]
    from tooltip_ocr import fields
    labels = fields({'lines': item.text.splitlines()})[1]
    materials = {label for label in labels if label.endswith('材料')}
    slots = labels & {'装备槽', '装饰槽', '雕刻槽'}
    if len(materials) > 1 or (materials and slots):
        return replace(item, action='keep', custom_rule='', reason='补充物品类型字段冲突，保留')
    if item.kind and item.kind != rule['kind']:
        return replace(item, action='keep', custom_rule='', reason='补充物品类型与识别结果冲突，保留')
    label = 'Alt＋右键腐蚀' if rule['action'] == 'corrode' else 'Ctrl＋右键入库'
    return replace(item, action=rule['action'], kind=rule['kind'], custom_rule=rule['id'],
                   reason='补充物品：' + name + '，' + label)


def allowed(item, rules, action):
    if not item.custom_rule or item.action != action or item.empty:
        return False
    if any(word in compact(item.text).upper() for word in ('已锁定', '已鎖定', '解除锁定', '解除鎖定',
                                                          '已装备', '已裝備', 'EQUIPPED')):
        return False
    return any(row['id'] == item.custom_rule and row['name'] == item.name and row['kind'] == item.kind
               and row['action'] == action and (not row['qualities'] or item.quality in row['qualities'])
               for row in normalize_rules(rules))
