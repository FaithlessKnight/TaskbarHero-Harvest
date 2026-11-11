"""Small fixed UI templates; search only pixels from the supplied frame."""
from functools import lru_cache

from windows_io import Frame, ROOT


@lru_cache(maxsize=24)
def asset(name):
    return Frame.load_bmp(ROOT / 'assets' / (name + '.bmp'))


def find_matches(frame, reference, region=None, *, mean_limit=8, changed_limit=0.06):
    left, top, width, height = region or (0, 0, frame.width, frame.height)
    right = min(frame.width, left + width) - reference.width
    bottom = min(frame.height, top + height) - reference.height
    # Bright/chromatic template pixels reject wrong candidates quickly. The
    # complete patch comparison still supplies the final decision.
    samples = [(x, y) for y in range(reference.height) for x in range(reference.width)
               if max(reference.pixels[(y * reference.width + x) * 4:(y * reference.width + x) * 4 + 3]) >= 90]
    samples = samples[::max(1, len(samples) // 16)][:16]
    if not samples:
        return []
    template = reference.template()
    matches = []
    for y in range(max(0, top), bottom + 1):
        for x in range(max(0, left), right + 1):
            if any(max(abs(frame.pixels[((y + dy) * frame.width + x + dx) * 4 + c]
                           - reference.pixels[(dy * reference.width + dx) * 4 + c])
                       for c in range(3)) > 35 for dx, dy in samples):
                continue
            if frame.crop([x, y, reference.width, reference.height]).matches(template, mean_limit, changed_limit):
                matches.append([x, y])
    return matches


def header_qualities(frame):
    hits = []
    for name, quality in (('rarity-ultimate', '至宝'), ('rarity-celestial', '天界')):
        if find_matches(frame, asset(name), [48, 28, min(100, frame.width - 48), min(42, frame.height - 28)],
                        mean_limit=4, changed_limit=0.025):
            hits.append(quality)
    # Tiny green/blue rarity text is often omitted by Windows OCR. Match its
    # complete glyphs only in this tooltip's rarity line, never its title.
    region = [48, 28, min(100, frame.width - 48), min(34, frame.height - 28)]
    mask = name_mask(frame)
    for name, quality in (('rarity-uncommon', '罕见'), ('rarity-rare', '稀有'), ('rarity-rare-sap', '稀有'),
                          ('rarity-uncommon-orb', '罕见'), ('rarity-uncommon-scroll', '罕见'),
                          ('rarity-transcendent-bow', '超凡'), ('rarity-transcendent-ingot', '超凡')):
        if find_matches(mask, name_mask(asset(name)), region,
                        mean_limit=1, changed_limit=0.005):
            if quality not in hits:
                hits.append(quality)
    return hits


def name_mask(frame):
    # Name colors change with rarity; preserve their bright pixel strokes.
    pixels = bytearray(frame.width * frame.height * 4)
    for n in range(0, len(pixels), 4):
        value = 255 if max(frame.pixels[n:n + 3]) >= 55 else 0
        pixels[n:n + 4] = bytes([value, value, value, 255])
    return Frame(frame.width, frame.height, bytes(pixels))


def header_names(frame):
    reference = name_mask(asset('plague-fruit-name'))
    # Only the tooltip title can authorize the exception, never its effects
    # or an item name mentioned in a lower description/comparison panel.
    if frame.width < reference.width or frame.height < reference.height:
        return []
    header = frame.crop([0, 0, frame.width, min(36, frame.height)])
    return ['瘟疫之果'] if find_matches(name_mask(header), reference,
                                      mean_limit=1, changed_limit=0.005) else []


def header_material_types(frame):
    # Exclude the type icon and the effect list. Colors vary with material
    # type, so compare the complete text silhouette in the header only.
    region = [60, 54, min(110, frame.width - 60), min(30, frame.height - 54)]
    mask = name_mask(frame)
    return [label for name, label in (('material-decoration', '装饰材料'),
                                      ('material-inscription', '铭刻材料'), ('material-engraving', '雕刻材料'))
            if find_matches(mask, name_mask(asset(name)), region,
                            mean_limit=1, changed_limit=0.005)]


def header_equipment_stats(frame):
    # A primary stat in the icon header identifies an equipment type. Lower
    # effect lists on materials cannot provide this evidence.
    region = [52, 54, min(100, frame.width - 52), min(35, frame.height - 54)]
    mask = name_mask(frame)
    return [label for name, label in (('stat-cooldown-reduction', '冷却缩减'),
                                      ('stat-attack-damage', '攻击伤害'))
            if find_matches(mask, name_mask(asset(name)), region, mean_limit=1, changed_limit=.005)]
