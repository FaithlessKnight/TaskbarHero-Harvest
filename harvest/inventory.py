"""Track scrolling by matching overlapping rows, without assuming wheel speed."""

from windows_io import Frame, ROOT


def sort_button(frame, config):
    marked = config.get('points', {}).get('inventory_sort')
    if marked is not None:
        return marked
    reference = Frame.load_bmp(ROOT / 'assets' / 'inventory-sort.bmp')
    first, last = config['points']['inventory_first'], config['points']['inventory_last']
    step_x = (last[0] - first[0]) / (config['bag_columns'] - 1)
    step_y = (last[1] - first[1]) / (config['bag_rows'] - 1)
    left, right = max(0, round(first[0] - step_x)), min(frame.width - reference.width, round(last[0] + step_x))
    top = max(0, round(last[1] + step_y * 0.4))
    bottom = min(frame.height - reference.height, round(last[1] + step_y * 2))
    samples = [(x, y) for y in range(0, reference.height, 3) for x in range(0, reference.width, 3)]
    candidates = []
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            total = 0
            for dx, dy in samples:
                n, r = ((y + dy) * frame.width + x + dx) * 4, (dy * reference.width + dx) * 4
                total += max(abs(frame.pixels[n + c] - reference.pixels[r + c]) for c in range(3))
                if total > 8 * len(samples):
                    break
            else:
                if frame.crop([x, y, reference.width, reference.height]).matches(reference.template(), 8, 0.04):
                    candidates.append([x + reference.width // 2, y + reference.height // 2])
    if len(candidates) != 1:
        raise RuntimeError('无法唯一识别背包整理按钮。请在校准页标记“背包整理按钮”，保存后重新开始收菜。')
    return candidates[0]


def same_cells(before, after):
    return len(before) == len(after) and all(a.matches(b.template(), 6, 0.06) for a, b in zip(before, after))


def same_warehouse_cells(before, after):
    """Include small bright count glyphs that the whole-cell tolerance hides."""
    if not same_cells(before, after):
        return False
    for first, second in zip(before, after):
        changed = 0
        for y in range(first.height // 2, first.height):
            for x in range(first.width // 2, first.width):
                n = (y * first.width + x) * 4
                a, b = first.pixels[n:n + 3], second.pixels[n:n + 3]
                if a == b or max(abs(a[c] - b[c]) for c in range(3)) <= 30:
                    continue
                digit = any(max(color) >= 140 and max(color) - min(color) <= 55 for color in (a, b))
                if digit:
                    changed += 1
                    if changed >= 3:
                        return False
    return True


def shifted_rows(before, after, columns, rows):
    matches = []
    for shift in range(1, rows):
        if same_cells(before[shift * columns:], after[:(rows - shift) * columns]):
            matches.append(shift)
    return matches[0] if len(matches) == 1 else None


class InventoryPager:
    def __init__(self, game, config, points, boxes, neutral, note):
        self.game, self.config, self.points, self.boxes = game, config, points, boxes
        self.neutral, self.note = neutral, note
        self.columns, self.rows = config['bag_columns'], config['bag_rows']
        self.total_rows = config.get('bag_total_rows', 15)
        self.top = 0
        self.row_delta = None

    def snapshot(self):
        frame = self.game.frame()
        return [frame.crop(box) for box in self.boxes]

    def wheel(self, delta):
        # The last visible slot belongs to the backpack scroll viewport.
        self.game.scroll(self.points[-1], delta)
        self.neutral()

    def reset(self):
        if self.total_rows <= self.rows:
            self.top = 0
            return
        self.neutral()
        # Large upward movement, then confirm the visible contents have settled
        # at the top. Never rely on the user's starting scroll position.
        self.wheel(120 * self.total_rows)
        previous = self.snapshot()
        stable = 0
        for unused in range(self.total_rows + 3):
            self.wheel(120)
            current = self.snapshot()
            stable = stable + 1 if same_cells(previous, current) else 0
            if stable >= 2:
                self.top = 0
                return
            previous = current
        raise RuntimeError('无法确认背包已滚到顶部，已停止。')

    def advance(self):
        if self.top + self.rows >= self.total_rows:
            return 0
        self.neutral()
        before = self.snapshot()
        amount = 0
        for attempt in range(60):
            delta = self.row_delta if attempt == 0 and self.row_delta else 10
            self.wheel(-delta)
            amount += delta
            current = self.snapshot()
            shift = shifted_rows(before, current, self.columns, self.rows)
            if shift is not None and not same_cells(before, current):
                if self.top + shift + self.rows > self.total_rows:
                    break
                self.top += shift
                self.row_delta = max(1, round(amount / shift))
                self.note(f'背包向下滚动 {shift} 行，当前显示第 {self.top + 1}～{self.top + self.rows} 行。')
                return shift
        self.row_delta = None
        raise RuntimeError('背包滚动后无法确认行对齐，已停止，避免漏格或重复处理。请将背包滚回顶部后重试。')

    def seek(self, top):
        if self.top > top:
            self.reset()
        while self.top < top:
            if not self.advance():
                raise RuntimeError('无法定位背包物品所在行，已停止。')

    def point(self, slot):
        local = slot - self.top * self.columns
        if not 0 <= local < len(self.points):
            raise RuntimeError(f'背包第 {slot + 1} 格不在当前可见区域，已停止。')
        return self.points[local]
