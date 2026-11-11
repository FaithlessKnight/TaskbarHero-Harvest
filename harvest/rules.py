"""Conservative item decisions, independent of Windows input and OCR."""
from collections import Counter
from dataclasses import asdict, dataclass
import re
import unicodedata


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def added_lines(lines, baseline):
    remaining = Counter(compact(line) for line in baseline)
    result = []
    for line in lines:
        key = compact(line)
        if remaining[key]:
            remaining[key] -= 1
        else:
            result.append(line)
    return result


def has_item_details(lines, baseline=()):
    fresh = [compact(line) for line in added_lines(lines, baseline)]
    quality = QUALITY_NAMES + OTHER_QUALITIES
    return is_plague_fruit(fresh) or any(re.fullmatch('(?:' + '|'.join(quality) + r')(?:级|級|報|报)?', line) for line in fresh) or any(
        storage_rarity_line(line) for line in fresh[:6]) or any(
        any(label in line for label in ('供奉材料', '供养材料', '供獻材料', '供献材料',
                                       '装备槽', '装饰槽', '制作材料', '装饰材料', '雕刻材料', '铭刻材料')) for line in fresh)


@dataclass
class Item:
    slot: int
    action: str
    quality: str
    kind: str
    level: int | None
    reason: str
    text: str
    image: str = ""
    view_top: int = 0
    empty: bool = False
    count: int | None = 1
    crafting: bool = False
    count_location: str = 'cube'
    material_type: str = ''
    name: str = ''
    custom_rule: str = ''
    source_count: int | None = None

    def to_dict(self):
        return asdict(self)

    def identity(self):
        # Retain item text, not screen position, to recheck before transferring it.
        stable = [compact(line) for line in self.text.splitlines()
                  if not re.fullmatch(r'[\d\s,.:/，]+', line)]
        return tuple(stable), self.action, self.level


QUALITY_NAMES = ("普通", "罕见", "稀有", "传奇")
OTHER_QUALITIES = ("不朽", "至宝", "奥秘", "超凡", "天界", "神圣", "宇宙")
WAREHOUSE_QUALITIES = ('至宝', '超凡', '天界', '神圣', '宇宙')
SYNTHESIS_QUALITIES = ('传奇', '不朽')
SELECTABLE_QUALITIES = ("普通", "罕见", "稀有", "传奇", "不朽", "至宝")
CORROSION_TYPES = {'equipment': '装备', 'material': '材料'}
MATERIAL_NAMES = {'crafting': '制作材料', 'decoration': '装饰材料', 'engraving': '雕刻材料'}
INSCRIPTION_CORROSION_QUALITIES = ('普通', '罕见', '稀有')
INSCRIPTION_STORAGE_QUALITIES = tuple(q for q in QUALITY_NAMES + OTHER_QUALITIES
                                      if q not in INSCRIPTION_CORROSION_QUALITIES)


def is_plague_fruit(lines):
    for line in list(lines)[:6]:
        title = compact(line)
        if title == '瘟疫之果':
            return True
        # A separately recognized rarity may precede the title. Once any
        # other name/text appears, a later description cannot supply a name.
        if not title or re.fullmatch(r'[0Oo丨·.。]*(?:' + '|'.join(QUALITY_NAMES + OTHER_QUALITIES)
                                    + r')(?:级|級|報|报)?', title) or storage_rarity_line(title):
            continue
        return False
    return False


def storage_rarity_line(text):
    # Restrict observed OCR suffix errors to a short header line. Item names,
    # effect descriptions and comparison text cannot supply a storage rarity.
    match = re.fullmatch(r'[0Oo丨·.。]*(' + '|'.join(WAREHOUSE_QUALITIES)
                         + r')(?:级|級|報|报|鳅|鍛|龌|龊)?', compact(text))
    return match[1] if match else None


def corrosion_selection(value=None):
    """Migrate missing settings; an explicit empty selection stays empty."""
    if value is None:
        return {kind: list(SELECTABLE_QUALITIES[:5 if kind == 'material' else 3])
                for kind in CORROSION_TYPES}
    if not isinstance(value, dict) or set(value) - (set(CORROSION_TYPES) | set(MATERIAL_NAMES)):
        raise ValueError('腐蚀勾选配置无效，请重新设置。')
    result = {}
    for kind in set(CORROSION_TYPES) | set(MATERIAL_NAMES):
        qualities = value.get(kind, [])
        if not isinstance(qualities, list) or any(q not in SELECTABLE_QUALITIES for q in qualities):
            raise ValueError('腐蚀勾选品质无效，请重新设置。')
        result[kind] = [q for q in SELECTABLE_QUALITIES if q in qualities]
    if 'material' not in value:
        result['material'] = [q for q in SELECTABLE_QUALITIES
                              if any(q in result[k] for k in MATERIAL_NAMES)]
    return {k: result[k] for k in CORROSION_TYPES}


def selected_for_corrosion(item, selection=None):
    if item.material_type == 'plague_fruit' and is_plague_fruit(item.text.splitlines()):
        return True
    if item.material_type == 'inscription':
        return item.kind == 'material' and item.quality in INSCRIPTION_CORROSION_QUALITIES
    return (item.material_type != 'offering' and item.quality not in WAREHOUSE_QUALITIES
            and item.quality in corrosion_selection(selection).get(item.kind, []))


REQUIRED_LEVEL_LABEL = r'需[要苤]等[级級]?'


def required_level_values(text):
    return {int(n) for line in text.splitlines()
            for n in re.findall(REQUIRED_LEVEL_LABEL + r'[:：]?(\d{1,3})(?!\d)', compact(line))}


def parse_required_level(text):
    """Only the required-level label in the hovered tooltip supplies a level."""
    hits = required_level_values(text)
    return next(iter(hits)) if len(hits) == 1 and all(1 <= n <= 999 for n in hits) else None


def parse_level(icon_text):
    text = compact(icon_text)
    hits = set(int(n) for n in re.findall(r"(?i)(?<![a-z])Lv[.·:]?(\d{1,3})(?!\d)", text))
    # This fallback is only for the isolated level strip of an equipment icon.
    if not hits and re.fullmatch(r'\d{1,3}', text):
        hits = {int(text)}
    return next(iter(hits)) if len(hits) == 1 and all(1 <= n <= 999 for n in hits) else None


def parse_stack_repetition(text, copies):
    # Single tiny digits are often ignored by Windows OCR. The same cropped
    # glyph is repeated; accept only an exact run of the same 2..5 digit.
    # The game's small 5 is consistently read as s/S. This alias applies only
    # to the isolated repeated quantity glyph, never to general tooltip text.
    value = compact(text).translate(str.maketrans({'s': '5', 'S': '5'}))
    return int(value[0]) if len(value) == copies and value[0] in '2345' and len(set(value)) == 1 else None


def classify(slot, lines, baseline=(), icon_text="", full_text="", selection=None, quality_evidence=()):
    tooltip_lines = added_lines(lines, baseline)
    text = "\n".join([*quality_evidence, *tooltip_lines])
    all_flat = compact(full_text or "\n".join(lines))
    crafting = False
    material_type = ''
    prefix = r'(?:[^\w\u4e00-\u9fff]|[0Oo一二三十]){0,3}'

    def item(action="keep", quality="", kind="", level=None, reason="无法可靠识别，保留"):
        return Item(slot, action, quality, kind, level, reason, text,
                    crafting=crafting, material_type=material_type)

    if any(word in all_flat for word in ("已锁定", "已鎖定", "解除锁定", "解除鎖定")):
        return item(reason="锁定物品，交由手动处理")
    # Supplemental header glyphs must not push the last original OCR header
    # line out of consideration, especially a conflicting higher rarity.
    quality_lines = [*quality_evidence, *tooltip_lines[:6]]
    quality_hits = {q for line in quality_lines for q in QUALITY_NAMES + OTHER_QUALITIES
                    if re.fullmatch(r'[0Oo丨·.。]*' + q + r'[级級報报]', compact(line))}
    # Windows OCR sometimes omits the small suffix on a pixel-font rarity line.
    quality_hits.update(compact(line) for line in quality_lines
                        if compact(line) in QUALITY_NAMES + OTHER_QUALITIES)
    quality_hits.update(q for line in quality_lines if (q := storage_rarity_line(line)))
    if is_plague_fruit(tooltip_lines):
        material_type = 'plague_fruit'
        quality = next(iter(quality_hits)) if len(quality_hits) == 1 else ''
        return item('corrode', quality, 'material', reason='瘟疫之果，不限品质优先腐蚀')
    if any(re.fullmatch(prefix + r'(?:供奉|供养|供獻|供献)材料', compact(line)) for line in tooltip_lines):
        material_type = 'offering'
        quality = next(iter(quality_hits)) if len(quality_hits) == 1 else ''
        return item('store', quality, 'material', reason='供奉材料，Ctrl＋右键入库')
    # A fragment or description mentioning offerings cannot confirm a type.
    if any(word in all_flat for word in ("供奉", "供养", "供獻", "供献")):
        return item(reason="供奉材料类型未确认，保留")
    if len(quality_hits) != 1:
        return item(reason="品质缺失或冲突，保留")
    quality = quality_hits.pop()
    slots = any(re.fullmatch(prefix + r'(?:装[备備]槽|[装裝][饰飾][槽稽]|雕刻槽)', compact(line))
                for line in tooltip_lines)
    stats = any(re.fullmatch(prefix + r'(?:防御力|攻击力|攻击伤害|攻击速度|冷[却卻](?:缩|縮)?[减減]|格[挡擋][几鸟鳥]率)[\d+.,%\-]*', compact(line))
                for line in tooltip_lines)
    # Only accept a material type line; descriptions can mention other materials.
    material = any(re.fullmatch(prefix + r"(?:制作|製作|装饰|裝飾|雕刻|铭刻|銘刻)?材料",
                               compact(line)) for line in tooltip_lines)
    soulstone = any(re.fullmatch(prefix + r'[灵靈员員][魂魄]石', compact(line)) for line in tooltip_lines)
    types = {key for key, label in (('crafting', '(?:制作|製作)'),
                                    ('decoration', '(?:装饰|裝飾)'), ('engraving', '雕刻'),
                                    ('inscription', '(?:铭刻|銘刻)'))
             if any(re.fullmatch(prefix + label + '材料',
                                 compact(line)) for line in tooltip_lines)}
    material_type = next(iter(types)) if len(types) == 1 else ''
    crafting = material_type == 'crafting'
    kind = '' if slots and material else 'material' if material or (soulstone and quality in WAREHOUSE_QUALITIES) else 'equipment' if slots or stats else ''
    if soulstone:
        material_type = 'soulstone'
    level = parse_required_level(text)
    if 'inscription' in types:
        if types != {'inscription'} or kind != 'material':
            return item(quality=quality, reason='铭刻材料类型冲突，保留')
        if quality in INSCRIPTION_STORAGE_QUALITIES:
            return item('store', quality, kind, level, '铭刻材料：非普通、罕见、稀有品质，Ctrl＋右键入库')
        return item('corrode', quality, kind, level, '铭刻材料：普通、罕见、稀有品质优先腐蚀')
    if quality in WAREHOUSE_QUALITIES:
        return item('store', quality, kind, level, '指定高品质，Ctrl＋右键入库')
    if not kind:
        return item(quality=quality, reason="物品类型未确认，保留")
    candidate = item(quality=quality, kind=kind, level=level)
    if selected_for_corrosion(candidate, selection):
        return item('corrode', quality, kind, level,
                    '已勾选腐蚀：' + quality + CORROSION_TYPES[kind])
    if quality in SYNTHESIS_QUALITIES and kind == "equipment":
        return item("synthesize", quality, kind, level, quality + "装备，按同品质每9件合成")
    return item(quality=quality, kind=kind, level=level, reason="不在处理范围，保留")


def batches(items):
    storage = [i for i in items if i.action == 'store']
    corrosion = [i for i in items if i.action == "corrode"]
    result = [('store', [item]) for item in storage]
    result += [("corrode", corrosion[n:n + 9]) for n in range(0, len(corrosion), 9)]
    for quality in SYNTHESIS_QUALITIES:
        group = [i for i in items if i.action == "synthesize" and i.quality == quality and i.kind == 'equipment']
        result.extend(("synthesize", group[n:n + 9]) for n in range(0, len(group) - 8, 9))
    return result
