"""Read small header text independently of a long tooltip's OCR size cap."""
import re

from rules import compact, QUALITY_NAMES, OTHER_QUALITIES, storage_rarity_line


def header_view(frame):
    # Coordinates refer to a framed tooltip with the four-pixel OCR margin.
    # Keep title/effects outside the view used for exact type/quality evidence.
    scale = (frame.width - 8) / 226
    if not .7 <= scale <= 1.25:
        return None
    left, top = round(48 * scale), round(28 * scale)
    right, bottom = frame.width - 4, min(frame.height - 4, round(98 * scale))
    if right <= left or bottom - top < round(48 * scale):
        return None
    return frame.crop([left, top, right - left, bottom - top]).padded(8)


def fields(result):
    qualities, labels = set(), set()
    blocked = False
    for line in result['lines']:
        value = compact(line)
        if any(word in value.upper() for word in ('已锁定', '已鎖定', '解除锁定', '解除鎖定',
                                                  '已装备', '已裝備', 'EQUIPPED')):
            blocked = True
        hit = re.fullmatch(r'[0Oo丨·.。]*(' + '|'.join(QUALITY_NAMES + OTHER_QUALITIES)
                           + r')(?:[级級報报])?', value)
        quality = hit[1] if hit else storage_rarity_line(value)
        if quality:
            qualities.add(quality)
        match = re.fullmatch(r'[0Oo一二三十|丨·.。]*(制作材料|製作材料|装饰材料|裝飾材料|雕刻材料|'
                             r'铭刻材料|銘刻材料|供奉材料|供养材料|供獻材料|供献材料|'
                             r'装备槽|裝備槽|装饰槽|裝飾槽|雕刻槽|'
                             r'防御力|攻击力|攻击伤害|攻击速度|冷却缩减|冷卻縮減|格挡几率)[\d+.,%\-]*', value)
        if match:
            label = match[1].translate(str.maketrans('製裝飾銘備卻縮減', '制装饰铭备却缩减'))
            labels.add('供奉材料' if label in ('供养材料', '供獻材料', '供献材料') else label)
    return qualities, labels, blocked


def consensus(results):
    parsed = [fields(result) for result in results]
    qs = [value[0] for value in parsed]
    labels = [value[1] for value in parsed]
    agreed = set.intersection(*labels) if labels else set()
    materials = {label for group in labels for label in group if label.endswith('材料')}
    observed = set.union(*qs) if qs else set()
    return {'qualities': sorted(set.intersection(*qs) if qs else set()),
            'observed_qualities': sorted(observed), 'labels': sorted(agreed),
            'blocked': (any(value[2] for value in parsed) or len(materials) > 1
                        or ('供奉材料' in materials and '供奉材料' not in agreed)),
            'text': '\n'.join(result['text'] for result in results)}
