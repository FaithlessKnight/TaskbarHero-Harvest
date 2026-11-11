"""Locate numbered warehouse tabs and confirm a page before transferring."""
from dataclasses import dataclass
from enum import Enum

from inventory import same_warehouse_cells
from vision import asset, find_matches
from windows_io import Frame


class StorageResult(Enum):
    FULL = 'all_pages_full'


@dataclass
class Pages:
    points: list[list[int]]
    current: int


def tab_mask(frame):
    # The page numerals turn from beige to white on the selected tab. Both
    # keep the same strokes, unlike the surrounding brown/gold button.
    pixels = bytearray(frame.width * frame.height * 4)
    for n in range(0, len(pixels), 4):
        b, g, r = frame.pixels[n:n + 3]
        value = 255 if b >= 30 and g >= 60 and r >= 75 else 0
        pixels[n:n + 4] = bytes([value, value, value, 255])
    return Frame(frame.width, frame.height, bytes(pixels))


def tab_region(config, frame):
    first, last = config['points']['warehouse_first'], config['points']['warehouse_last']
    dx = (last[0] - first[0]) / (config['warehouse_columns'] - 1)
    dy = (last[1] - first[1]) / (config['warehouse_rows'] - 1)
    left, top = max(0, round(first[0] - dx)), max(0, round(first[1] - dy * 2))
    right, bottom = min(frame.width, round(last[0] + dx)), min(frame.height, round(first[1] - dy * .5))
    return [left, top, max(0, right - left), max(0, bottom - top)]


def locate(config, frame):
    x, y, width, height = tab_region(config, frame)
    if min(width, height) <= 0:
        return None
    masked = tab_mask(frame.crop([x, y, width, height]))
    positions = []
    # These are the four numbered tabs in the supplied UI. The add-page
    # control must immediately follow the last one; it is never clicked.
    for number in range(1, 5):
        reference = tab_mask(asset(f'warehouse-page-{number}'))
        hits = find_matches(masked, reference, mean_limit=1, changed_limit=.005)
        if len(hits) > 1:
            return None
        positions.append(hits[0] if hits else None)
    count = next((n for n, point in enumerate(positions) if point is None), 4)
    if count < 1 or any(point is not None for point in positions[count:]):
        return None
    positions = positions[:count]
    if any(abs(positions[n][0] - positions[0][0] - n * 36) > 1 for n in range(count)):
        return None
    selected_y = min(point[1] for point in positions)
    selected = [n for n, point in enumerate(positions) if point[1] == selected_y]
    if len(selected) != 1 or any(point[1] not in (selected_y, selected_y + 4) for point in positions):
        return None
    plus = asset('warehouse-pages-add')
    adds = find_matches(frame.crop([x, y, width, height]).binary(35, border=0),
                        plus.binary(35, border=0), mean_limit=1, changed_limit=.005)
    if not any(abs(px - (positions[0][0] + count * 36 - 9)) <= 1
               and abs(py - (selected_y - 3)) <= 1 for px, py in adds):
        return None
    return Pages([[x + px + 6, y + py + 7] for px, py in positions], selected[0] + 1)


class WarehousePager:
    def __init__(self, engine):
        self.e = engine
        self.pages = self.read(engine.game.frame())

    def read(self, frame):
        pages = locate(self.e.config, frame)
        if pages is None:
            path = self.e.save_state('warehouse-tabs-unrecognized')
            raise RuntimeError('无法确认仓库编号页签及当前页，已暂停，不重复点击。截图：' + path)
        return pages

    def select(self, number, item):
        e = self.e
        e.neutral()
        before = e.game.frame()
        pages = self.read(before)
        if len(pages.points) != len(self.pages.points) or not 1 <= number <= len(pages.points):
            raise RuntimeError('仓库页签数量发生变化，请恢复仓库页面后继续。')
        if pages.current == number:
            self.pages = pages
            return
        from engine import cell_box
        source_box = cell_box(e.config, 'inventory', e.inventory_point(item.slot))
        source = before.crop(source_box)
        mode = getattr(e, 'current_mode', 'corrode')
        board = e.occupied(mode, frame=before)
        e.note(f'切换到仓库第 {number} 页。')
        e.game.click(pages.points[number - 1])
        e.neutral()
        previous, stable = None, 0
        for unused in range(12):
            frame = e.game.frame()
            current = locate(e.config, frame)
            if not frame.crop(source_box).matches(source.template(), 3, .02) or e.occupied(mode, frame=frame) != board:
                raise RuntimeError('仓库翻页时背包原物品或魔方槽位变化，未再次入库。')
            if current is not None and current.current == number and len(current.points) == len(pages.points):
                cells = e.warehouse_cells(frame)
                stable = stable + 1 if previous is not None and same_warehouse_cells(previous, cells) else 1
                if stable >= 3:
                    self.pages = current
                    return
                previous = cells
            else:
                previous, stable = None, 0
            e.game.wait(.25)
        path = e.save_state('warehouse-page-unconfirmed')
        raise RuntimeError('仓库翻页未确认，已暂停，未再次放入物品。截图：' + path)
