"""Restore tooltip headers and verify displaced tooltips against the bag icon."""
from vision import asset, find_matches


def distance(box, point):
    x, y, width, height = box
    return max(x - point[0], 0, point[0] - x - width) ** 2 + max(
        y - point[1], 0, point[1] - y - height) ** 2


def framed_region(frame, box, step):
    x, y, width, height = box
    left, top = max(0, x - round(step * .5)), max(0, y - 36)
    right, bottom = min(frame.width, x + width + round(step * .5)), min(frame.height, y + 24)
    region = [left, top, right - left, bottom - top]
    pairs = []
    for lx, ly in find_matches(frame, asset('tooltip-top-left'), region, mean_limit=4, changed_limit=.02):
        for rx, ry in find_matches(frame, asset('tooltip-top-right'), region, mean_limit=4, changed_limit=.02):
            if abs(ly - ry) <= 1 and step * 2 <= rx - lx <= step * 8 and lx <= x + 4 and rx >= x + width - 16:
                # Normalize relative to the paired title-frame corners, keeping
                # the entire rarity/type/icon area above the dark body crop.
                x0, y0 = max(0, lx - 3), max(0, ly - 5)
                x1, y1 = min(frame.width, rx + 13), min(frame.height, y + height + 4)
                pairs.append([x0, y0, x1 - x0, y1 - y0])
    return pairs[0] if len(pairs) == 1 else None


def icon_matches(frame, before, box, source, *, diagnostics=None):
    x, y, width, height = box
    # Match the central sprite, excluding item borders, level labels and
    # bottom-right stack digits. Both game icons use the same native pixels.
    inset_x, inset_y = max(1, source[2] // 5), max(1, source[3] // 6)
    sample = [source[0] + inset_x, source[1] + inset_y,
              max(1, source[2] * 3 // 5), max(1, source[3] // 2)]
    reference = before.crop(sample)
    if not frame.crop(sample).matches(reference.template(), 3, .02):
        return False
    pixels = [reference.pixels[n:n + 3] for n in range(0, len(reference.pixels), 4)]
    if sum(max(p) >= 90 and max(p) - min(p) >= 30 for p in pixels) < len(pixels) * .15:
        return False
    region = [x + 10, y + 36, min(48, width - 10), min(54, height - 36)]
    if min(region[2:]) <= 0:
        return False
    if find_matches(frame, reference, region, mean_limit=8, changed_limit=.06):
        if diagnostics is not None:
            diagnostics['matched_by'] = 'central-sprite'
        return True
    # Equipment has a Lv label across the lower inventory icon, absent from
    # the larger tooltip icon. Keep strict pixel thresholds and instead use
    # two separate upper patches that must agree on the same translation.
    width, height = source[2] * 3 // 8, source[3] * 3 // 8
    if min(width, height) < 8:
        return False
    offsets = [(source[2] // 8, source[3] // 8), (source[2] // 2, source[3] // 8)]
    translations = None
    for dx, dy in offsets:
        sample = [source[0] + dx, source[1] + dy, width, height]
        patch = before.crop(sample)
        if not frame.crop(sample).matches(patch.template(), 3, .02):
            return False
        colors = [patch.pixels[n:n + 3] for n in range(0, len(patch.pixels), 4)]
        if (sum(max(p) >= 90 and max(p) - min(p) >= 30 for p in colors) < len(colors) * .15
                or len(set(colors)) < 6):
            return False
        hits = {(px - dx, py - dy) for px, py in find_matches(
            frame, patch, region, mean_limit=4, changed_limit=.02)}
        translations = hits if translations is None else translations & hits
        if not translations:
            return False
    matched = len(translations) == 1
    if matched and diagnostics is not None:
        diagnostics.update(matched_by='two-upper-patches', icon_origin=[list(p) for p in translations])
    return matched


def item_regions(frame, before, point, step, source, *, diagnostics=None):
    boxes = frame.tooltip_boxes(before, point, step, include_distant=True)
    framed = [(box, framed_region(frame, box, step)) for box in boxes]
    if diagnostics is not None:
        diagnostics.update(point=list(point), source=list(source), frame_pixels=[frame.width, frame.height],
                           candidates=[{'dark_box': box, 'framed_box': normalized} for box, normalized in framed])
    near = [(box, normalized) for box, normalized in framed if distance(box, point) <= (step * 1.5) ** 2]
    if near:
        # Preserve the user's nearest-tooltip rule for ordinary comparison UI.
        selected = [normalized or box for box, normalized in near]
        if diagnostics is not None:
            diagnostics.update(selected=selected, method='nearest')
        return selected
    distant = []
    for index, (box, normalized) in enumerate(framed):
        evidence = {}
        if normalized is not None and icon_matches(frame, before, normalized, source, diagnostics=evidence):
            distant.append(normalized)
        if diagnostics is not None:
            diagnostics['candidates'][index].update(evidence)
    # A comparison with the same sprite is ambiguous; never merge its OCR.
    selected = distant if len(distant) == 1 else []
    if diagnostics is not None:
        diagnostics.update(selected=selected, method='unique-source-icon' if selected else
                           'ambiguous-source-icons' if distant else 'source-icon-unconfirmed')
    return selected
